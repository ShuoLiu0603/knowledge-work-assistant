from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.core.config import Settings, validate_runtime_settings
from app.db.models.agent_run import AgentRun
from app.db.models.conversation import Conversation, Message
from app.db.models.retrieval_log import RetrievalLog
from app.db.models.user_memory import UserMemoryEvent, UserMemoryUpdateJob
from app.workers.celery_app import celery_app
from app.workers.memory_tasks import (
    claim_memory_update_job,
    complete_memory_update_job,
    list_recoverable_memory_update_job_ids,
    list_summary_recovery_candidates,
    process_memory_update_job,
    recover_deferred_agent_memory_updates,
    recover_stale_conversation_summaries,
    recover_stale_memory_update_jobs,
    update_conversation_summary_task,
)
from app.memory.jobs import claim_memory_update_job_dispatch
from helpers import create_user, isolated_session


class WorkerReliabilityTests(unittest.TestCase):
    def test_celery_uses_late_ack_worker_loss_rejection_and_beat_schedules(self) -> None:
        self.assertTrue(celery_app.conf.task_acks_late)
        self.assertTrue(celery_app.conf.task_reject_on_worker_lost)
        self.assertEqual(celery_app.conf.worker_prefetch_multiplier, 1)
        self.assertIsNone(process_memory_update_job.max_retries)
        self.assertTrue(process_memory_update_job.acks_late)
        self.assertTrue(process_memory_update_job.reject_on_worker_lost)

        schedules = celery_app.conf.beat_schedule
        self.assertEqual(
            schedules["recover-stale-memory-update-jobs"]["task"],
            "recover_stale_memory_update_jobs",
        )
        self.assertEqual(
            schedules["apply-operational-retention-daily"]["task"],
            "apply_operational_retention",
        )
        self.assertEqual(
            schedules["recover-stale-external-cleanup-jobs"]["task"],
            "recover_stale_external_cleanup_jobs",
        )
        self.assertEqual(
            schedules["recover-deferred-agent-memory-updates"]["task"],
            "recover_deferred_agent_memory_updates",
        )
        self.assertEqual(
            schedules["recover-stale-conversation-summaries"]["task"],
            "recover_stale_conversation_summaries",
        )
        self.assertFalse(schedules["apply-operational-retention-daily"]["kwargs"]["dry_run"])

    def test_claim_preserves_per_user_job_order(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "ordered-memory-jobs@example.com", "Ordered Memory Jobs")
            first = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="My preferred language is Chinese",
                created_at=now,
                updated_at=now,
            )
            second = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="My preferred language is English",
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
            session.add_all([first, second])
            session.commit()

            blocked = claim_memory_update_job(
                session,
                second.id,
                lease_token="second-token",
                now=now + timedelta(seconds=2),
                lease_seconds=60,
            )
            self.assertEqual(blocked.outcome, "blocked")
            self.assertEqual(blocked.job.attempts, 0)

            first_claim = claim_memory_update_job(
                session,
                first.id,
                lease_token="first-token",
                now=now + timedelta(seconds=2),
                lease_seconds=60,
            )
            self.assertEqual(first_claim.outcome, "claimed")
            self.assertEqual(first_claim.job.attempts, 1)

            still_blocked = claim_memory_update_job(
                session,
                second.id,
                lease_token="second-token",
                now=now + timedelta(seconds=3),
                lease_seconds=60,
            )
            self.assertEqual(still_blocked.outcome, "blocked")

            completed = complete_memory_update_job(
                session,
                first.id,
                lease_token="first-token",
                actions=[],
                now=now + timedelta(seconds=4),
            )
            self.assertEqual(completed.status, "completed")

            second_claim = claim_memory_update_job(
                session,
                second.id,
                lease_token="second-token",
                now=now + timedelta(seconds=5),
                lease_seconds=60,
            )
            self.assertEqual(second_claim.outcome, "claimed")
            self.assertEqual(second_claim.job.attempts, 1)

    def test_active_lease_prevents_duplicate_claim_and_token_mismatch_writeback(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "leased-memory-job@example.com", "Leased Memory Job")
            job = UserMemoryUpdateJob(user_id=user.id, user_message="I prefer concise answers")
            session.add(job)
            session.commit()

            claimed = claim_memory_update_job(
                session,
                job.id,
                lease_token="owner-token",
                now=now,
                lease_seconds=60,
            )
            duplicate = claim_memory_update_job(
                session,
                job.id,
                lease_token="duplicate-token",
                now=now + timedelta(seconds=1),
                lease_seconds=60,
            )

            self.assertEqual(claimed.outcome, "claimed")
            self.assertEqual(duplicate.outcome, "leased")
            self.assertEqual(duplicate.job.attempts, 1)
            self.assertIsNone(
                complete_memory_update_job(
                    session,
                    job.id,
                    lease_token="duplicate-token",
                    actions=[],
                    now=now + timedelta(seconds=2),
                )
            )
            self.assertEqual(session.get(UserMemoryUpdateJob, job.id).status, "processing")

    def test_job_completion_commits_memory_side_effects_only_for_the_current_lease(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "memory-fence@example.com", "Memory Fence")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="remember this",
                status="processing",
                lease_token="current-lease",
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            session.add(job)
            session.commit()

            rolled_back_event = UserMemoryEvent(
                id="rolled-back-memory-event",
                user_id=user.id,
                event_type="touch",
                reason="must roll back with stale lease",
            )
            session.add(rolled_back_event)
            stale_result = complete_memory_update_job(
                session,
                job.id,
                lease_token="stale-lease",
                actions=[],
            )

            self.assertIsNone(stale_result)
            self.assertIsNone(session.get(UserMemoryEvent, rolled_back_event.id))
            self.assertEqual(session.get(UserMemoryUpdateJob, job.id).status, "processing")

            committed_event = UserMemoryEvent(
                user_id=user.id,
                event_type="touch",
                reason="commit with current lease",
            )
            session.add(committed_event)
            completed = complete_memory_update_job(
                session,
                job.id,
                lease_token="current-lease",
                actions=[{"action": "touch"}],
            )

            self.assertEqual(completed.status, "completed")
            self.assertIsNotNone(session.get(UserMemoryEvent, committed_event.id))

    def test_expired_lease_can_be_reclaimed(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "expired-memory-job@example.com", "Expired Memory Job")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="Remember my current project",
                status="processing",
                attempts=1,
                lease_token="dead-worker-token",
                lease_expires_at=now - timedelta(seconds=1),
            )
            session.add(job)
            session.commit()

            reclaimed = claim_memory_update_job(
                session,
                job.id,
                lease_token="replacement-token",
                now=now,
                lease_seconds=60,
            )

            self.assertEqual(reclaimed.outcome, "claimed")
            self.assertEqual(reclaimed.job.attempts, 2)
            self.assertEqual(reclaimed.job.lease_token, "replacement-token")

    def test_final_failed_job_requires_manual_requeue_before_claim(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "failed-memory-job@example.com", "Failed Memory Job")
            earlier = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="Earlier queued work must not reactivate a failed job",
                status="queued",
                created_at=now - timedelta(seconds=1),
                updated_at=now - timedelta(seconds=1),
            )
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="Do not automatically run me again",
                status="failed",
                attempts=4,
                error_message="retry budget exhausted",
                created_at=now,
                updated_at=now,
            )
            session.add_all([earlier, job])
            session.commit()

            claim = claim_memory_update_job(
                session,
                job.id,
                lease_token="late-delivery-token",
                now=now,
                lease_seconds=60,
            )

            self.assertEqual(claim.outcome, "unavailable")
            self.assertEqual(claim.job.status, "failed")
            self.assertEqual(claim.job.attempts, 4)

    def test_recovery_finds_expired_processing_and_orphaned_queued_jobs(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "recover-memory-jobs@example.com", "Recover Memory Jobs")
            stale_processing = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="stale processing",
                status="processing",
                lease_token="dead-worker",
                lease_expires_at=now - timedelta(seconds=1),
                updated_at=now - timedelta(minutes=5),
            )
            active_processing = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="active processing",
                status="processing",
                lease_token="live-worker",
                lease_expires_at=now + timedelta(minutes=5),
                updated_at=now,
            )
            orphaned_queued = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="orphaned queued",
                status="queued",
                error_message="worker dispatch failed: broker unavailable",
                updated_at=now - timedelta(minutes=5),
            )
            committed_not_dispatched = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="committed before process crash",
                status="queued",
                error_message="",
                dispatched_at=None,
                updated_at=now - timedelta(minutes=5),
            )
            recently_dispatched = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="waiting in broker",
                status="queued",
                error_message="",
                dispatched_at=now,
                updated_at=now - timedelta(minutes=5),
            )
            fresh_queued = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="fresh queued",
                status="queued",
                updated_at=now,
            )
            session.add_all(
                [
                    stale_processing,
                    active_processing,
                    orphaned_queued,
                    committed_not_dispatched,
                    recently_dispatched,
                    fresh_queued,
                ]
            )
            session.commit()

            recoverable = set(list_recoverable_memory_update_job_ids(session, now=now))

            self.assertEqual(
                recoverable,
                {stale_processing.id, orphaned_queued.id, committed_not_dispatched.id},
            )

    def test_recovery_dispatch_claim_suppresses_repeat_delivery_during_cooldown(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "recovery-cooldown@example.com", "Recovery Cooldown")
            job = UserMemoryUpdateJob(
                user_id=user.id,
                user_message="expired processing",
                status="processing",
                lease_token="dead-worker",
                lease_expires_at=now - timedelta(seconds=1),
                dispatched_at=None,
                updated_at=now - timedelta(minutes=20),
            )
            session.add(job)
            session.commit()

            self.assertIn(job.id, list_recoverable_memory_update_job_ids(session, now=now))
            self.assertTrue(
                claim_memory_update_job_dispatch(
                    session,
                    job.id,
                    statuses=("processing",),
                    redispatch_before=now - timedelta(minutes=10),
                    now=now,
                )
            )
            self.assertNotIn(
                job.id,
                list_recoverable_memory_update_job_ids(
                    session,
                    now=now + timedelta(minutes=1),
                ),
            )

    def test_recovery_task_redispatches_each_recoverable_job(self) -> None:
        with (
            patch("app.workers.memory_tasks.init_db"),
            patch(
                "app.workers.memory_tasks.list_recoverable_memory_update_job_ids",
                return_value=["job-1", "job-2"],
            ),
            patch(
                "app.workers.memory_tasks.memory_jobs.claim_memory_update_job_dispatch",
                return_value=True,
            ),
            patch("app.workers.memory_tasks.SessionLocal"),
            patch.object(process_memory_update_job, "delay") as delay,
        ):
            result = recover_stale_memory_update_jobs()

        self.assertEqual(result["dispatched_job_ids"], ["job-1", "job-2"])
        self.assertEqual([call.args[0] for call in delay.call_args_list], ["job-1", "job-2"])

    def test_summary_task_rechecks_trigger_and_updates_in_its_own_session(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "summary-worker@example.com", "Summary Worker")
            conversation = Conversation(user_id=user.id, title="Summary worker test")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            with (
                patch("app.workers.memory_tasks.init_db"),
                patch("app.workers.memory_tasks.SessionLocal", return_value=session),
                patch(
                    "app.workers.memory_tasks.acquire_conversation_run_lease",
                    return_value=SimpleNamespace(key="summary-lease", token="token"),
                ) as acquire_lease,
                patch("app.workers.memory_tasks.release_conversation_run_lease") as release_lease,
                patch("app.workers.memory_tasks.should_update_conversation_summary", return_value=True) as should_update,
                patch("app.workers.memory_tasks.update_conversation_summary", return_value="updated summary") as update_summary,
            ):
                result = update_conversation_summary_task(conversation.id, user.id)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["summary_length"], len("updated summary"))
            should_update.assert_called_once_with(session, conversation.id)
            update_summary.assert_called_once_with(session, conversation, "", "", user.id)
            acquire_lease.assert_called_once()
            release_lease.assert_called_once()

    def test_summary_recovery_redispatches_each_candidate(self) -> None:
        with (
            patch("app.workers.memory_tasks.init_db"),
            patch("app.workers.memory_tasks.SessionLocal"),
            patch(
                "app.workers.memory_tasks.list_summary_recovery_candidates",
                return_value=[("conversation-1", "user-1"), ("conversation-2", "user-2")],
            ),
            patch.object(update_conversation_summary_task, "delay") as delay,
        ):
            result = recover_stale_conversation_summaries()

        self.assertEqual(result["conversation_ids"], ["conversation-1", "conversation-2"])
        self.assertEqual(
            [call.args for call in delay.call_args_list],
            [("conversation-1", "user-1"), ("conversation-2", "user-2")],
        )

    def test_summary_recovery_candidates_find_unprocessed_conversations(self) -> None:
        with isolated_session() as session:
            user = create_user(session, "summary-recovery@example.com", "Summary Recovery")
            conversation = Conversation(user_id=user.id, title="Needs summary")
            session.add(conversation)
            session.commit()
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content="unprocessed message",
                )
            )
            session.commit()

            with patch("app.workers.memory_tasks.should_update_conversation_summary", return_value=True):
                candidates = list_summary_recovery_candidates(session)

            self.assertEqual(candidates, [(conversation.id, user.id)])

    def test_deferred_memory_recovery_waits_for_committed_assistant_and_replays_once(self) -> None:
        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        with isolated_session() as session:
            user = create_user(session, "deferred-memory@example.com", "Deferred Memory")
            conversation = Conversation(user_id=user.id, title="Deferred", search_scope="accessible")
            session.add(conversation)
            session.commit()
            source = Message(
                conversation_id=conversation.id,
                role="user",
                content="remember this",
                created_at=now,
            )
            assistant = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="ack",
                created_at=now + timedelta(seconds=1),
            )
            retrieval_log = RetrievalLog(
                user_id=user.id,
                conversation_id=conversation.id,
                scope_type="accessible",
                searched_knowledge_base_ids=[],
                question=source.content,
                rewritten_query=source.content,
            )
            session.add_all([source, assistant, retrieval_log])
            session.commit()
            run = AgentRun(
                user_id=user.id,
                conversation_id=conversation.id,
                message_id=source.id,
                retrieval_log_id=retrieval_log.id,
                input=source.content,
                intent="chat",
                status="completed",
                answer=assistant.content,
                state={
                    "message_id": source.id,
                    "memory_actions": [{"action": "deferred"}],
                },
                updated_at=now - timedelta(minutes=5),
            )
            session.add(run)
            session.commit()

            settings = SimpleNamespace(
                memory_update_mode="async",
                memory_update_job_recovery_interval_seconds=60,
                worker_recovery_scan_multiplier=5,
            )
            with (
                patch("app.workers.memory_tasks.init_db"),
                patch("app.workers.memory_tasks.SessionLocal", return_value=nullcontext(session)),
                patch("app.workers.memory_tasks.get_settings", return_value=settings),
                patch(
                    "app.workers.memory_tasks.apply_deferred_memory_update",
                    side_effect=lambda _db, current_run, **_kwargs: current_run,
                ) as apply_memory,
            ):
                result = recover_deferred_agent_memory_updates()

            self.assertEqual(result["agent_run_ids"], [run.id])
            apply_memory.assert_called_once_with(session, run, source_message_id=source.id)
            session.refresh(run)
            session.refresh(retrieval_log)
            self.assertEqual(run.message_id, assistant.id)
            self.assertEqual(retrieval_log.message_id, assistant.id)

    def test_deferred_memory_recovery_is_enabled_for_sync_mode(self) -> None:
        settings = SimpleNamespace(memory_update_mode="sync")
        with (
            patch("app.workers.memory_tasks.init_db"),
            patch("app.workers.memory_tasks.SessionLocal"),
            patch("app.workers.memory_tasks.get_settings", return_value=settings),
            patch("app.workers.memory_tasks.list_deferred_agent_run_ids", return_value=[]),
        ):
            result = recover_deferred_agent_memory_updates()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recovered_count"], 0)

    def test_memory_and_retention_settings_reject_invalid_bounds(self) -> None:
        for values in (
            {"short_memory_max_messages": 0},
            {"conversation_summary_max_unprocessed": 0},
            {"llm_call_log_retention_days": -1},
            {"memory_update_job_lease_seconds": 0},
            {"operational_retention_hour_utc": 24},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(**values)

    def test_production_lease_covers_memory_llm_call_budget(self) -> None:
        settings = Settings(
            app_env="production",
            database_url="postgresql+psycopg://user:strong-db-password@postgres:5432/rag_app",
            auto_create_tables=False,
            jwt_secret_key="a-long-random-secret-value-for-prod",
            backend_cors_origins="https://app.example.com",
            llm_api_key="test-llm-key",
            embedding_api_key="test-embedding-key",
            minio_access_key="prod-minio-access",
            minio_secret_key="prod-minio-secret",
            llm_timeout_seconds=30,
            memory_update_job_lease_seconds=90,
        )

        with self.assertRaises(RuntimeError) as error:
            validate_runtime_settings(settings)

        self.assertIn("memory review and operation LLM call budget", str(error.exception))


if __name__ == "__main__":
    unittest.main()
