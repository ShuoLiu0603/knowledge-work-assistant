export function isLikelyEmail(value: string): boolean {
  return value.includes("@") && value.includes(".");
}

export function isStrongEnoughPassword(value: string): boolean {
  return value.length >= 8;
}
