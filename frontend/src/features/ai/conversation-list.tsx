import Link from "next/link";
import { ErrorState } from "@/components/ui/states";
import { cn } from "@/lib/cn";
import { referenceOf } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/datetime";
import type { Paginated } from "@/lib/api/types";
import type { AiConversation } from "@/types/api/ai";

/**
 * The signed-in user's own conversations (GET ai/conversations/ is
 * owner-only: organization membership never exposes a colleague's questions),
 * most recent activity first (AIConversation.Meta.ordering). Links select a
 * conversation through `?conversation=` on the same page.
 */
export function ConversationList({
  result,
  activeId,
  timeZone,
}: {
  result: { ok: true; data: Paginated<AiConversation> } | { ok: false; error: Error };
  activeId: string | null;
  timeZone: string;
}) {
  return (
    <nav aria-label="Conversations" className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-900">Conversations</h2>
        <Link href="/ai" className="text-xs font-medium text-brand-700 hover:underline">
          New conversation
        </Link>
      </div>
      {!result.ok ? (
        <ErrorState compact title="Could not load conversations" message={result.error.message} reference={referenceOf(result.error)} />
      ) : result.data.results.length === 0 ? (
        <p className="text-xs text-ink-500">Your questions will be listed here.</p>
      ) : (
        <>
          <ul className="flex flex-col gap-1">
            {result.data.results.map((conversation) => {
              const active = conversation.id === activeId;
              return (
                <li key={conversation.id}>
                  <Link
                    href={`/ai?conversation=${conversation.id}`}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "block rounded-md px-2 py-1.5 text-sm",
                      active ? "bg-brand-50 font-medium text-brand-800" : "text-ink-700 hover:bg-ink-50",
                    )}
                  >
                    <span className="line-clamp-2 break-words">{conversation.title || "Untitled conversation"}</span>
                    <span className="block text-2xs text-ink-500">
                      {formatDateTime(conversation.last_message_at ?? conversation.created_at, { timeZone })}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
          {result.data.count > result.data.results.length ? (
            <p className="text-2xs text-ink-500">
              Showing the {result.data.results.length} most recent of {result.data.count}.
            </p>
          ) : null}
        </>
      )}
    </nav>
  );
}
