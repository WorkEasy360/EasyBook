"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { FormError, FormField, Textarea } from "@/components/ui/field";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import type { AiMessage, AiSource, AiStructuredData, AskRequest, AskResponse } from "@/types/api/ai";
import { AnswerView } from "./answer-view";
import { AI_DISABLED_MESSAGE, isAiDisabledError } from "./sources";

/**
 * The Ask Books conversation: POST ai/ask/.
 *
 * - Each submission carries one Idempotency-Key, so a retried request replays
 *   the first answer instead of spending the rate limit twice (AskView).
 * - A disabled or unavailable assistant shows exactly AI_DISABLED_MESSAGE —
 *   never a placeholder answer.
 * - Every other failure (rate limit, monthly quota, timeout, a too-long
 *   question) shows the backend's own message and reference.
 * - BACKEND CONTRACT BLOCKER: nothing in the ask response (or any endpoint
 *   every role can call) says which provider answered. The dev backend runs
 *   the `fake` LLM with AI_ALLOW_FAKE_PROVIDERS, and its answers are
 *   indistinguishable here from a real provider's; only GET ai/usage/
 *   (Owner/Admin) reveals `provider: "fake"`.
 * - After the first answer the conversation id from the response is kept, so
 *   follow-ups continue the same thread; router.refresh() updates the
 *   conversation list without remounting this component.
 */

type ChatEntry =
  | { kind: "question"; key: string; text: string }
  | {
      kind: "answer";
      key: string;
      answer: string;
      status: string;
      sources: AiSource[];
      structuredData: AiStructuredData | null;
      warnings: string[];
    };

function fromMessages(messages: AiMessage[]): ChatEntry[] {
  return messages.map((message) =>
    message.role === "user"
      ? { kind: "question", key: message.id, text: message.content }
      : {
          kind: "answer",
          key: message.id,
          answer: message.content,
          status: message.status,
          sources: message.sources,
          // Stored messages do not keep structured_data or warnings.
          structuredData: null,
          warnings: [],
        },
  );
}

export function AskBooksChat({
  conversationId: initialConversationId,
  initialMessages,
}: {
  conversationId: string | null;
  initialMessages: AiMessage[];
}) {
  const router = useRouter();
  const formId = React.useId();
  const [entries, setEntries] = React.useState<ChatEntry[]>(() => fromMessages(initialMessages));
  const [conversationId, setConversationId] = React.useState<string | null>(initialConversationId);
  const [question, setQuestion] = React.useState("");
  const [disabled, setDisabled] = React.useState(false);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);
  const endRef = React.useRef<HTMLDivElement>(null);

  const mutation = useIdempotentMutation<AskResponse, AskRequest>(
    "ai/conversations",
    (input, idempotencyKey) => api.post<AskResponse>("ai/ask", input, { idempotencyKey, timeoutMs: 90_000 }),
    {
      onSuccess: (response, input) => {
        setDisabled(false);
        setEntries((current) => [
          ...current,
          { kind: "question", key: `${response.request_id}-q`, text: input.question },
          {
            kind: "answer",
            key: `${response.request_id}-a`,
            answer: response.answer,
            status: response.status,
            sources: response.sources,
            structuredData: response.structured_data,
            warnings: response.warnings,
          },
        ]);
        if (response.conversation_id) setConversationId(response.conversation_id);
        setQuestion("");
        router.refresh();
      },
      onError: (failure) => {
        if (isAiDisabledError(failure)) {
          setDisabled(true);
          return;
        }
        setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) });
      },
    },
  );

  const pendingQuestion = mutation.isPending ? mutation.variables?.question : undefined;

  React.useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [entries.length, pendingQuestion]);

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (!text || mutation.isPending) return;
    setError(null);
    setDisabled(false);
    mutation.mutate(conversationId ? { question: text, conversation_id: conversationId } : { question: text });
  }

  function startOver() {
    if (initialConversationId) {
      // An opened conversation lives in the URL; leaving it is a navigation.
      router.push("/ai");
      return;
    }
    setEntries([]);
    setConversationId(null);
    setQuestion("");
    setError(null);
    setDisabled(false);
  }

  return (
    <div className="flex flex-col gap-4">
      {entries.length > 0 ? (
        <div className="flex justify-end">
          <Button variant="link" size="sm" onClick={startOver} disabled={mutation.isPending}>
            Start a new conversation
          </Button>
        </div>
      ) : null}
      <ol className="flex flex-col gap-4" aria-label="Conversation">
        {entries.length === 0 && !pendingQuestion ? (
          <li className="rounded-lg border border-dashed border-ink-200 px-4 py-6 text-center text-sm text-ink-500">
            Ask about your books — for example “What was net profit last quarter?” or “Which invoices are overdue?”
          </li>
        ) : null}
        {entries.map((entry) =>
          entry.kind === "question" ? (
            <li key={entry.key} className="self-end rounded-lg bg-brand-50 px-3 py-2 sm:max-w-[80%]">
              <p className="sr-only">You asked:</p>
              <p className="text-sm break-words whitespace-pre-wrap text-ink-900">{entry.text}</p>
            </li>
          ) : (
            <li key={entry.key} className="rounded-lg border border-ink-200 bg-white px-4 py-3">
              <p className="mb-2 text-2xs font-semibold tracking-wide text-ink-500 uppercase">Ask Books</p>
              <AnswerView
                answer={entry.answer}
                status={entry.status}
                sources={entry.sources}
                structuredData={entry.structuredData}
                warnings={entry.warnings}
              />
            </li>
          ),
        )}
        {pendingQuestion ? (
          <li className="self-end rounded-lg bg-brand-50 px-3 py-2 opacity-80 sm:max-w-[80%]" aria-busy="true">
            <p className="sr-only">You asked:</p>
            <p className="text-sm break-words whitespace-pre-wrap text-ink-900">{pendingQuestion}</p>
          </li>
        ) : null}
      </ol>
      <div ref={endRef} />

      {disabled ? (
        <div role="alert" className="rounded-md border border-ink-200 bg-ink-50 px-3 py-2 text-sm text-ink-800">
          {AI_DISABLED_MESSAGE}
        </div>
      ) : null}

      <form id={formId} method="post" noValidate onSubmit={onSubmit} className="flex flex-col gap-2">
        <FormField
          label={conversationId ? "Follow-up question" : "Your question"}
          hint="Answers use only what your role can already see in EasyBook."
        >
          <Textarea
            rows={3}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter keeps a newline — the usual chat contract.
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
        </FormField>
        <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        <div className="flex justify-end">
          <Button
            type="submit"
            variant="primary"
            loading={mutation.isPending}
            loadingLabel="Ask Books is working…"
            disabled={!question.trim()}
          >
            Ask
          </Button>
        </div>
      </form>
    </div>
  );
}
