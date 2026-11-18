// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const { SSO_ONLY_RETRY_MS, SSO_ONLY_TTL_MS, createSsoOnlyProbe, isSsoOnlyMethods } = await import("./sso-only");

const methods = (password: boolean, workeasy360: boolean) => ({ password, workeasy360, workeasy360SignOut: false });

/** A probe whose backend gives these answers in turn (the last one repeats), on a clock the test moves. */
function probeWith(...answers: Array<ReturnType<typeof methods> | null | Error>) {
  let at = 1_000_000;
  const queue = [...answers];
  const ask = vi.fn(async () => {
    const answer = queue.length > 1 ? queue.shift() : queue[0];
    if (answer instanceof Error) throw answer;
    return answer ?? null;
  });
  const probe = createSsoOnlyProbe({ ask, now: () => at });
  return {
    probe,
    ask,
    advance: (ms: number) => {
      at += ms;
    },
  };
}

describe("isSsoOnlyMethods", () => {
  it("is Workeasy360 on and EasyBook's password sign-in off", () => {
    expect(isSsoOnlyMethods(methods(false, true))).toBe(true);
    expect(isSsoOnlyMethods(methods(true, true))).toBe(false);
    expect(isSsoOnlyMethods(methods(true, false))).toBe(false);
    // Nothing to sign in with is a misconfiguration to show on the sign-in page, not to redirect into.
    expect(isSsoOnlyMethods(methods(false, false))).toBe(false);
  });
});

describe("the SSO-only probe", () => {
  it("asks the backend once per window, however many visitors arrive", async () => {
    const { probe, ask, advance } = probeWith(methods(false, true));
    expect(await Promise.all([probe(), probe(), probe()])).toEqual([true, true, true]);
    expect(ask).toHaveBeenCalledTimes(1);

    advance(SSO_ONLY_TTL_MS - 1);
    expect(await probe()).toBe(true);
    expect(ask).toHaveBeenCalledTimes(1);

    advance(1);
    expect(await probe()).toBe(true);
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it("follows the backend when the setting changes", async () => {
    const { probe, advance } = probeWith(methods(false, true), methods(true, true));
    expect(await probe()).toBe(true);
    advance(SSO_ONLY_TTL_MS);
    expect(await probe()).toBe(false);
  });

  it("does not treat what it cannot find out as SSO-only", async () => {
    const { probe } = probeWith(null);
    expect(await probe()).toBe(false);
  });

  it("keeps the last answer through an outage and waits before asking again", async () => {
    const { probe, ask, advance } = probeWith(methods(false, true), null);
    expect(await probe()).toBe(true);

    advance(SSO_ONLY_TTL_MS);
    expect(await probe()).toBe(true);
    expect(ask).toHaveBeenCalledTimes(2);

    // Inside the retry back-off: no further question.
    advance(SSO_ONLY_RETRY_MS - 1);
    expect(await probe()).toBe(true);
    expect(ask).toHaveBeenCalledTimes(2);

    advance(1);
    expect(await probe()).toBe(true);
    expect(ask).toHaveBeenCalledTimes(3);
  });

  it("treats a thrown question as an outage", async () => {
    const { probe } = probeWith(new Error("boom"));
    expect(await probe()).toBe(false);
  });
});
