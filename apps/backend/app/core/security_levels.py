MIN_SECURITY_LEVEL = 1
MAX_SECURITY_LEVEL = 5
DEFAULT_SECURITY_LEVEL = 1


def validate_security_level(value: int) -> int:
    if value < MIN_SECURITY_LEVEL or value > MAX_SECURITY_LEVEL:
        raise ValueError(f"security_level must be between {MIN_SECURITY_LEVEL} and {MAX_SECURITY_LEVEL}")
    return value
