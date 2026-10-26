// A one-time intake handoff only establishes this browser's task ownership.
// Authorization is enforced by the server; do not expose the token in UI errors.
const INVALID_LINK = '任务链接无法接管，请重新获取链接。';

export async function claimLocalTask({ location, history, selected, api, toast } = {}) {
  const notify = () => { try { if (typeof toast === 'function') toast(INVALID_LINK); } catch {} };
  try {
    const hash = String(location?.hash || '');
    const fragment = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
    if (!fragment.has('claim')) return false;

    // Remove the entire fragment before validation or any network operation.
    // If removal is unavailable or fails, do not transmit the token.
    history.replaceState(null, '', location.pathname + location.search);
    const tokens = fragment.getAll('claim');
    const token = tokens[0];
    if (tokens.length !== 1 || !/^[0-9a-f]{64}$/.test(token) || typeof selected !== 'string' || !/^[0-9a-f]{32}$/.test(selected)) {
      notify(); return false;
    }
    await api(`/api/jobs/${selected}/claim`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Justbuild-Intent': 'intake-claim' },
      body: JSON.stringify({ token }),
    });
    return true;
  } catch {
    notify(); return false;
  }
}
