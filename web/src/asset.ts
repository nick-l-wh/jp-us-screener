export function asset(path: string): string {
  const embedded = document.getElementById("asset:" + path);
  if (embedded?.textContent) return embedded.textContent;
  return (import.meta.env.BASE_URL || "./") + path;
}
