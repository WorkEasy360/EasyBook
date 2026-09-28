import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { Card, CardBody } from "@/components/ui/card";
import { LinkButton } from "@/components/ui/link-button";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { AskBooksChat } from "@/features/ai/ask-books-chat";
import { NonAuthoritativeNotice } from "@/features/ai/answer-view";
import { ConversationList } from "@/features/ai/conversation-list";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { AiConversation, AiConversationDetail } from "@/types/api/ai";

export const metadata: Metadata = { title: "Ask Books" };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LIST_SIZE = 50;

/**
 * Ask Books. `?conversation=<id>` opens one of the user's conversations
 * (GET ai/conversations/{id}/, owner-only — another user's id is a 404).
 *
 * The assistant explains; it does not produce figures of record. Every answer
 * carries that notice and its citations (features/ai/answer-view.tsx).
 */
export default async function AskBooksPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.USE_AI_ASSISTANT)) {
    return (
      <>
        <PageHeader title="Ask Books" />
        <ForbiddenState resource="Ask Books" />
      </>
    );
  }

  const requested = paramOf(params, "conversation");
  const conversationId = requested && UUID_PATTERN.test(requested) ? requested : null;

  const [conversations, detail] = await Promise.all([
    tryServer(() => serverApi.list<AiConversation>("ai/conversations", { query: { page_size: LIST_SIZE } })),
    conversationId
      ? tryServer(() => serverApi.get<AiConversationDetail>(`ai/conversations/${conversationId}`))
      : Promise.resolve(null),
  ]);

  const missing = detail && !detail.ok && detail.error instanceof ApiError && detail.error.isNotFound;

  return (
    <>
      <PageHeader
        title="Ask Books"
        description="Questions about your books, answered from EasyBook's own reports and your documents."
        actions={
          roleHasPermission(session.role, PERMISSIONS.VIEW_AI_USAGE) ? (
            <LinkButton href="/ai/usage">Usage</LinkButton>
          ) : null
        }
      />
      <PageBody>
        <div className="grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <aside className="lg:border-r lg:border-ink-200 lg:pr-4">
            <ConversationList result={conversations} activeId={conversationId} timeZone={session.timeZone} />
          </aside>

          <div className="flex min-w-0 flex-col gap-4">
            <Card>
              <CardBody className="py-3">
                <NonAuthoritativeNotice className="text-sm text-ink-600" />
              </CardBody>
            </Card>

            {detail && !detail.ok ? (
              <ErrorState
                compact
                title={missing ? "Conversation not found" : "Could not load this conversation"}
                message={missing ? "It may have passed the retention period, or it belongs to someone else." : detail.error.message}
                reference={referenceOf(detail.error)}
              />
            ) : (
              <AskBooksChat
                // A different conversation is a different chat: remount so no
                // state carries across.
                key={detail?.ok ? detail.data.id : "new"}
                conversationId={detail?.ok ? detail.data.id : null}
                initialMessages={detail?.ok ? detail.data.messages : []}
              />
            )}
          </div>
        </div>
      </PageBody>
    </>
  );
}
