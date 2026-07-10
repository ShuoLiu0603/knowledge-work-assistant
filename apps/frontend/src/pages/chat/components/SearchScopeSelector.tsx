import type { SearchScope, KnowledgeBase } from "../../../lib/api";
import styles from "./SearchScopeSelector.module.css";

type Props = {
  targetScope: SearchScope;
  onChange: (scope: SearchScope) => void;
  personalKbs: KnowledgeBase[];
  selectedKbId: string;
  onKbChange: (id: string) => void;
  disabled: boolean;
};

const SCOPE_OPTIONS: { value: SearchScope; label: string; adminOnly?: boolean }[] = [
  { value: "single", label: "单个知识库" },
  { value: "department", label: "部门知识库" },
  { value: "public", label: "公共知识库" },
  { value: "accessible", label: "所有知识库" },
];

export function SearchScopeSelector({ targetScope, onChange, personalKbs, selectedKbId, onKbChange, disabled }: Props) {
  return (
    <div className={styles.wrap}>
      <div className={styles.field}>
        <label className={styles.label}>检索目标</label>
        <select
          value={targetScope}
          onChange={(e) => onChange(e.target.value as SearchScope)}
          disabled={disabled}
          className={styles.select}
        >
          {SCOPE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value} disabled={opt.value === "single" && personalKbs.length === 0}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {targetScope === "single" && personalKbs.length > 0 && (
        <div className={styles.field}>
          <label className={styles.label}>知识库</label>
          <select
            value={selectedKbId}
            onChange={(e) => onKbChange(e.target.value)}
            disabled={disabled}
            className={styles.select}
          >
            {personalKbs.map((kb) => (
              <option key={kb.id} value={kb.id}>{kb.name}</option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
