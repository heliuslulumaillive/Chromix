import { splitProxy } from "./_network.js";

const RETIRED_AUTH_ENV = "CHROMIX_SOCKS5_AUTH";

export function browserProxy(value) {
  const proxy = splitProxy(value);
  if (proxy && /^socks(?:5h?)?:/.test(proxy.server) &&
      (proxy.username !== undefined || proxy.password !== undefined))
    throw new Error("Authenticated SOCKS5 requires a local proxy bridge");
  return proxy;
}

export function hasRetiredProxyAuth(env) {
  return Object.keys(env ?? {}).some(key => key.toUpperCase() === RETIRED_AUTH_ENV);
}

export function withoutRetiredProxyAuth(env) {
  return Object.fromEntries(Object.entries(env ?? {}).filter(([key]) => key.toUpperCase() !== RETIRED_AUTH_ENV));
}
