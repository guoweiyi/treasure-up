const marker = new URLSearchParams(window.location.search).get('client');
if (marker === 'native') {
  try {
    sessionStorage.setItem('treasure-native-shell', '1');
  } catch {
    /* Optional navigation preference. */
  }
}
export function inNativeShell() {
  try {
    return sessionStorage.getItem('treasure-native-shell') === '1';
  } catch {
    return false;
  }
}
