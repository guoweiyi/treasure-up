export function requiresLogin(path: string, protectedRoute: boolean, allowGuestAccess: boolean) {
  return path !== '/login' && (protectedRoute || !allowGuestAccess);
}

/** Keep the destination inside this site, including after URL normalization. */
export function loginDestination(value: unknown) {
  if (typeof value !== 'string' || !value.startsWith('/') || /[\\\u0000-\u001f]/.test(value))
    return '/';
  try {
    const target = new URL(value, 'https://treasure.invalid');
    if (target.origin !== 'https://treasure.invalid' || target.pathname === '/login') return '/';
    return target.pathname + target.search + target.hash;
  } catch {
    return '/';
  }
}
