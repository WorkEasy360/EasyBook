import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserMenu } from "./user-menu";

vi.mock("./theme-toggle", () => ({ ThemeToggle: () => null }));

const USER = { id: "u-1", email: "asha@example.com", first_name: "Asha", last_name: "Rao" };

async function openMenu() {
  await userEvent.click(screen.getByRole("button", { name: /Account menu for/ }));
}

describe("UserMenu sign-out", () => {
  const assign = vi.fn();

  beforeEach(() => {
    assign.mockReset();
    vi.stubGlobal("location", { ...window.location, assign });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function logoutResponds(response: Response | Error) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (response instanceof Error) throw response;
        return response;
      }),
    );
  }

  it("signs out of EasyBook and lands on the sign-in page", async () => {
    logoutResponds(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    render(<UserMenu user={USER} signOutOfWorkeasy360 />);
    await openMenu();

    await userEvent.click(screen.getByRole("button", { name: "Sign out of EasyBook" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
    expect(fetch).toHaveBeenCalledWith("/api/auth/logout", { method: "POST" });
  });

  it("carries on to Workeasy360 only after EasyBook's own session has ended", async () => {
    logoutResponds(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    render(<UserMenu user={USER} signOutOfWorkeasy360 />);
    await openMenu();

    await userEvent.click(screen.getByRole("button", { name: "Sign out of EasyBook and Workeasy360" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/api/auth/workeasy360/logout"));
    expect(fetch).toHaveBeenCalledWith("/api/auth/logout", { method: "POST" });
  });

  it.each([
    ["the server refuses", new Response(null, { status: 403 })],
    ["the server fails", new Response(null, { status: 500 })],
    ["the network fails", new Error("offline")],
  ])("stays, says so, and does NOT carry on to Workeasy360 when %s", async (_label, outcome) => {
    logoutResponds(outcome);
    render(<UserMenu user={USER} signOutOfWorkeasy360 />);
    await openMenu();

    await userEvent.click(screen.getByRole("button", { name: "Sign out of EasyBook and Workeasy360" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign you out. Please try again.");
    expect(assign).not.toHaveBeenCalled();
    // The buttons are usable again, so the person can try once more.
    expect(screen.getByRole("button", { name: "Sign out of EasyBook" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Sign out of EasyBook and Workeasy360" })).toBeEnabled();
  });

  it("stays on the page too when the plain sign-out fails", async () => {
    logoutResponds(new Response(null, { status: 500 }));
    render(<UserMenu user={USER} />);
    await openMenu();

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign you out");
    expect(assign).not.toHaveBeenCalled();
  });

  it("clears the error when the person tries again and it works", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response(null, { status: 500 }))
        .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 })),
    );
    render(<UserMenu user={USER} signOutOfWorkeasy360 />);
    await openMenu();

    await userEvent.click(screen.getByRole("button", { name: "Sign out of EasyBook and Workeasy360" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Sign out of EasyBook and Workeasy360" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/api/auth/workeasy360/logout"));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
