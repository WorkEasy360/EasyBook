"use client";

import * as React from "react";
import { cn } from "@/lib/cn";
import { Button, IconButton, type ButtonVariant } from "./button";
import { Icons } from "./icons";

/**
 * Modal dialog and side panel.
 *
 * Built on the native <dialog> element, which gives focus trapping, the
 * top layer, inert background and Escape handling from the platform rather
 * than from hand-written key handlers — the usual source of accessibility
 * bugs in a custom modal (spec §68).
 */

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  /**
   * Blocks dismissal by Escape or backdrop click. For a dialog with unsaved
   * input, so a stray Escape cannot discard a half-written journal.
   */
  dismissible?: boolean;
}

const SIZES = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
} as const;

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  dismissible = true,
}: DialogProps) {
  const ref = React.useRef<HTMLDialogElement>(null);
  const titleId = React.useId();
  const descriptionId = React.useId();

  React.useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      // showModal, not show: only the modal form gets the top layer, the
      // focus trap and the ::backdrop.
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  React.useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;

    function onCancel(event: Event) {
      // "cancel" is Escape. Preventing it is what makes a dialog undismissable.
      if (!dismissible) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      onClose();
    }

    dialog.addEventListener("cancel", onCancel);
    return () => dialog.removeEventListener("cancel", onCancel);
  }, [dismissible, onClose]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onClick={(event) => {
        // The backdrop is part of the <dialog> box, so a click landing on the
        // element itself (not on the inner panel) is a backdrop click.
        if (dismissible && event.target === ref.current) onClose();
      }}
      className={cn(
        "m-auto w-[calc(100vw-2rem)] rounded-lg border border-ink-200 bg-white p-0 shadow-overlay",
        "backdrop:bg-scrim",
        SIZES[size],
      )}
    >
      <div className="flex max-h-[85vh] flex-col">
        <div className="flex items-start justify-between gap-3 border-b border-ink-200 px-4 py-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-sm font-semibold text-ink-900">
              {title}
            </h2>
            {description ? (
              <p id={descriptionId} className="mt-0.5 text-xs text-ink-500">
                {description}
              </p>
            ) : null}
          </div>
          {dismissible ? (
            <IconButton
              label="Close"
              icon={<Icons.close className="size-4" />}
              size="sm"
              onClick={onClose}
            />
          ) : null}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>

        {footer ? (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-ink-200 bg-ink-50 px-4 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </dialog>
  );
}

/**
 * Confirmation for a destructive or irreversible action — voiding an invoice,
 * posting a journal, completing a reconciliation.
 *
 * The confirm button carries the action's own verb ("Void invoice"), never
 * "OK": people read the button, not the paragraph.
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel,
  variant = "danger",
  loading = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: React.ReactNode;
  confirmLabel: string;
  variant?: ButtonVariant;
  loading?: boolean;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      dismissible={!loading}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button variant={variant} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm text-ink-700">{message}</div>
    </Dialog>
  );
}

/**
 * Side panel for a secondary record without losing the list behind it — a
 * payment allocation, a bank-match candidate.
 */
export function SidePanel({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: "md" | "lg";
}) {
  const ref = React.useRef<HTMLDialogElement>(null);
  const titleId = React.useId();

  React.useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  React.useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    function onCancel(event: Event) {
      event.preventDefault();
      onClose();
    }
    dialog.addEventListener("cancel", onCancel);
    return () => dialog.removeEventListener("cancel", onCancel);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
      className={cn(
        "fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-dvh w-full rounded-none border-l border-ink-200 bg-white p-0 shadow-overlay",
        "backdrop:bg-scrim",
        width === "lg" ? "max-w-2xl" : "max-w-md",
      )}
    >
      <div className="flex h-full flex-col">
        <div className="flex items-start justify-between gap-3 border-b border-ink-200 px-4 py-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-sm font-semibold text-ink-900">
              {title}
            </h2>
            {description ? <p className="mt-0.5 text-xs text-ink-500">{description}</p> : null}
          </div>
          <IconButton
            label="Close"
            icon={<Icons.close className="size-4" />}
            size="sm"
            onClick={onClose}
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>

        {footer ? (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-ink-200 bg-ink-50 px-4 py-3">
            {footer}
          </div>
        ) : null}
      </div>
    </dialog>
  );
}
