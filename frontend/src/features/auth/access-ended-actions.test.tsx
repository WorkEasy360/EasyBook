import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { accessEndedPath } from "@/lib/auth/workeasy360-paths";
import { AccessEndedActions } from "./access-ended-actions";
import { accessEndedCopy } from "./access-ended-copy";

describe("AccessEndedActions", () => {
  const assign = vi.fn();

  beforeEach(() => {
    assign.mockReset();
    vi.stubGlobal("location", { ...window.location, assign });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("signs out of EasyBook and lands on sign-in", async () => {
    render(<AccessEndedActions />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
    expect(fetch).toHaveBeenCalledWith("/api/auth/logout", { method: "POST" });
  });

  it("offers no switch to a person with a single organization", () => {
    render(<AccessEndedActions />);
    expect(screen.queryByText("Switch organization")).toBeNull();
  });

  it("switches to another organization with a full load", async () => {
    render(<AccessEndedActions otherOrganizations={[{ id: "org-2", name: "Beta Traders" }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Beta Traders" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/dashboard"));
    expect(fetch).toHaveBeenCalledWith("/api/auth/organization", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ organizationId: "org-2" }),
    });
  });

  it("says so, and stays, when the switch is refused", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 403 })));
    render(<AccessEndedActions otherOrganizations={[{ id: "org-2", name: "Beta Traders" }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Beta Traders" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not switch organization.");
    expect(assign).not.toHaveBeenCalled();
  });

  it("sends a session with no Workeasy360 account to sign in, not to try again", () => {
    render(<AccessEndedActions signInRequired />);
    expect(screen.getByRole("link", { name: "Continue with Workeasy360" })).toHaveAttribute(
      "href",
      "/api/auth/workeasy360/start?next=%2Fdashboard",
    );
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("tries the app again", async () => {
    render(<AccessEndedActions />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(assign).toHaveBeenCalledWith("/dashboard");
  });
});

describe("ApiError.accessEndedReason", () => {
  it("reads the person's reason from the details the backend sends", () => {
    const error = new ApiError({
      status: 403, message: "ended", code: "workeasy360_access_ended", details: { reason: "no_seat" },
    });
    expect(error.accessEndedReason).toBe("no_seat");
    expect(new ApiError({ status: 403, message: "x", code: "workeasy360_access_ended" }).accessEndedReason).toBeNull();
  });
});

describe("a session that must sign in with Workeasy360", () => {
  it("is an access-ended refusal with its own page", () => {
    const error = new ApiError({ status: 403, message: "x", code: "workeasy360_sign_in_required" });
    expect(error.isAccessEnded).toBe(true);
    expect(accessEndedPath("workeasy360_sign_in_required", "no_seat")).toBe("/access-ended?reason=sign_in_required");
    expect(accessEndedCopy("sign_in_required").title).toBe("Sign in with Workeasy360");
  });
});

describe("accessEndedPath", () => {
  it("names a person's reason, and only a known one", () => {
    expect(accessEndedPath("workeasy360_access_ended", "membership_inactive")).toBe(
      "/access-ended?reason=membership_inactive",
    );
    expect(accessEndedPath("workeasy360_access_ended", "no_seat")).toBe("/access-ended?reason=no_seat");
    expect(accessEndedPath("workeasy360_access_ended", "expired")).toBe("/access-ended");
    expect(accessEndedPath("workeasy360_access_ended")).toBe("/access-ended");
    expect(accessEndedPath("workeasy360_access_unverified", "no_seat")).toBe("/access-ended?reason=unverified");
  });
});

describe("accessEndedCopy", () => {
  it("words each reason differently and defaults to the organization's ended access", () => {
    const titles = [undefined, "unverified", "membership_inactive", "no_seat"].map((r) => accessEndedCopy(r).title);
    expect(new Set(titles).size).toBe(4);
    expect(accessEndedCopy("no_seat").description).toMatch(/seat/i);
    expect(accessEndedCopy("membership_inactive").description).toMatch(/removed/i);
    expect(accessEndedCopy("anything-else")).toEqual(accessEndedCopy(undefined));
  });
});

describe("ApiError.isAccessEnded", () => {
  it("is only the Workeasy360 access-ended refusal", () => {
    const ended = new ApiError({ status: 403, message: "ended", code: "workeasy360_access_ended" });
    expect(ended.isAccessEnded).toBe(true);
    expect(new ApiError({ status: 403, message: "x", code: "workeasy360_read_only" }).isAccessEnded).toBe(false);
    expect(new ApiError({ status: 403, message: "x", code: "permission_denied" }).isAccessEnded).toBe(false);
  });
});
