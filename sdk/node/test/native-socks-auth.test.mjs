import { test } from "node:test";
import assert from "node:assert/strict";
import { browserProxy } from "../_socks_auth.js";
import { fontLaunchEnv } from "../_fonts.js";
import { buildLaunchOptions, buildContextOptions } from "../index.js";

test("authenticated SOCKS5 requires a broker before SDK startup", async () => {
  for (const proxy of ["socks5://user:password@localhost:1080",
                       {server:"socks5://[::1]:1080",username:"user",password:"password"}]) {
    assert.throws(() => browserProxy(proxy), /local proxy bridge/);
    assert.throws(() => buildContextOptions({contextOptions:{proxy}}), /local proxy bridge/);
    await assert.rejects(buildLaunchOptions({proxy}), /local proxy bridge/);
  }
  assert.deepEqual(browserProxy("socks5://localhost:1080"), {server:"socks5://localhost:1080"});
  assert.deepEqual(browserProxy("http://u:p@localhost:8000"), {server:"http://localhost:8000",username:"u",password:"p"});
});

test("unrelated launches do not inherit obsolete proxy credentials", t => {
  const key = "CHROMIX_SOCKS5_AUTH", previous = process.env[key];
  process.env[key] = "fixture";
  t.after(() => { if(previous===undefined) delete process.env[key]; else process.env[key]=previous; });
  assert.equal(Object.hasOwn(fontLaunchEnv(process.execPath, {KEEP:"yes"}),key),false);
  assert.equal(process.env[key],"fixture");
  assert.throws(() => fontLaunchEnv(process.execPath, {chromix_socks5_auth:"explicit"}),/retired/);
});
