"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getQuestionBank,
  createQuiz,
  answerQuizQuestion,
  finishQuiz,
} from "@/lib/api";
import { useTopics } from "@/lib/use-topics";
import { Button } from "@/components/ui/button";
import { QuizStartForm } from "./quiz-start-form";
import { QuizQuestionCard } from "./quiz-question-card";
import { QuizFeedback } from "./quiz-feedback";
import { QuizReview } from "./quiz-review";
import {
  formatQuizError,
  type QuizPhase,
  type QuizSession,
  type QuizErrorState,
} from "./quiz-types";

export interface QuizPanelProps {
  notebookId: string;
  uid: string;
  quizRequest: { topicIds: string[]; nonce: number } | null;
  hasOpenedQuiz: boolean;
}

export function QuizPanel({
  notebookId,
  uid,
  quizRequest,
  hasOpenedQuiz,
}: QuizPanelProps) {
  const [phase, setPhase] = useState<QuizPhase>("start");
  const [session, setSession] = useState<QuizSession | null>(null);
  const [selectedTopicIds, setSelectedTopicIds] = useState<string[]>([]);
  const [count, setCount] = useState<5 | 10>(5);
  const [currentDraft, setCurrentDraft] = useState("");
  const [error, setError] = useState<QuizErrorState | null>(null);

  // Preselect nonce tracking (H1, F4, F5)
  const [handledNonce, setHandledNonce] = useState(() => quizRequest?.nonce ?? 0);
  const [appliedTopicIds, setAppliedTopicIds] = useState<string[] | null>(null);

  // Refs for safety, session isolation, and timings
  const sessionRef = useRef(0);
  const inFlightRef = useRef(false);
  const startTimeRef = useRef(0);

  // Accessible heading refs for focus management (L10, F8)
  const questionHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const feedbackHeadingRef = useRef<HTMLHeadingElement | null>(null);

  // Queries
  const topicsQuery = useTopics(notebookId, uid);
  const questionBankQuery = useQuery({
    queryKey: ["question-bank", uid, notebookId],
    queryFn: () => getQuestionBank(notebookId),
    enabled: hasOpenedQuiz && !!notebookId && !!uid,
    staleTime: Infinity,
    retry: false,
  });

  // Calculate served question counts per topic (M6: mcq + numerical only)
  const servedCountsByTopic = useMemo(() => {
    const map = new Map<string, number>();
    const items = questionBankQuery.data?.items ?? [];
    for (const item of items) {
      if (item.type === "mcq" || item.type === "numerical") {
        map.set(item.topic_id, (map.get(item.topic_id) ?? 0) + item.verified);
      }
    }
    return map;
  }, [questionBankQuery.data]);

  // Topic names map for question headers and summary review
  const topicNameMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of topicsQuery.data?.items ?? []) {
      map.set(t.id, t.name);
    }
    return map;
  }, [topicsQuery.data]);

  // Adjust state during render when quizRequest changes (H1, F4, F5)
  if (quizRequest && quizRequest.nonce !== handledNonce) {
    setHandledNonce(quizRequest.nonce);
    if (phase === "start" || phase === "review") {
      setSelectedTopicIds(quizRequest.topicIds);
      setAppliedTopicIds(quizRequest.topicIds);
      setError(null);
      if (phase === "review") {
        setSession(null);
        setPhase("start");
      }
    }
  }

  // Focus effect: moves DOM focus to heading on phase/question change without setting state (L10, F8)
  const currentQuestionId = session?.quiz?.questions?.[session?.index]?.id;
  useEffect(() => {
    if (phase === "feedback") {
      feedbackHeadingRef.current?.focus();
    } else if (phase === "answering") {
      questionHeadingRef.current?.focus();
    }
  }, [phase, currentQuestionId]);

  // Start form controls
  const handleToggleTopic = (topicId: string) => {
    setAppliedTopicIds(null);
    if (phase === "start") setError(null);
    setSelectedTopicIds((prev) =>
      prev.includes(topicId)
        ? prev.filter((id) => id !== topicId)
        : [...prev, topicId]
    );
  };

  const handleCountChange = (newCount: 5 | 10) => {
    if (phase === "start") setError(null);
    setCount(newCount);
  };

  const handleClearStartError = () => {
    setError(null);
  };

  // Start Quiz handler (C4: in-flight guard, sessionRef increment, inFlightRef set true)
  const handleStartQuiz = async () => {
    if (inFlightRef.current) return;

    // Derive effective selection (only topics with verified questions)
    const effectiveSelectedIds = selectedTopicIds.filter(
      (id) => (servedCountsByTopic.get(id) ?? 0) > 0
    );
    if (effectiveSelectedIds.length < 1 || effectiveSelectedIds.length > 6) {
      return;
    }

    sessionRef.current += 1;
    const mySession = sessionRef.current;
    inFlightRef.current = true;
    setPhase("creating");
    setError(null);

    try {
      const quiz = await createQuiz(notebookId, {
        mode: "chosen",
        topic_ids: effectiveSelectedIds,
        count,
      });

      if (sessionRef.current !== mySession) return;

      if (quiz.questions.length === 0) {
        setError({
          code: "not_ready",
          source: "create",
          message: "There are no verified questions for these topics yet.",
          retryable: false,
        });
        setPhase("start");
        return;
      }

      setSession({
        quiz,
        index: 0,
        results: {},
        summary: null,
      });
      setCurrentDraft("");
      setPhase("answering");
      startTimeRef.current = performance.now();
    } catch (err) {
      if (sessionRef.current !== mySession) return;
      setError(formatQuizError(err, "create"));
      setPhase("start");
    } finally {
      if (sessionRef.current === mySession) {
        inFlightRef.current = false;
      }
    }
  };

  // Check Answer handler (H3, M9, C1)
  const handleCheckAnswer = async () => {
    if (inFlightRef.current || !session) return;
    const currentQ = session.quiz.questions[session.index];
    if (!currentQ || !currentDraft.trim()) return;

    const rawElapsed = performance.now() - startTimeRef.current;
    const time_ms = Math.max(0, Math.min(86400000, Math.round(rawElapsed)));

    const mySession = sessionRef.current;
    inFlightRef.current = true;
    setPhase("submitting");
    setError(null);

    const answerPayload =
      currentQ.type === "mcq" ? currentDraft : currentDraft.trim();

    try {
      const res = await answerQuizQuestion(notebookId, session.quiz.id, {
        question_id: currentQ.id,
        answer: answerPayload,
        time_ms,
      });

      if (sessionRef.current !== mySession) return;

      setSession((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          results: {
            ...prev.results,
            [currentQ.id]: {
              answer: res.answer,
              feedback: res.feedback,
              already_answered: res.already_answered,
            },
          },
        };
      });
      setPhase("feedback");
    } catch (err) {
      if (sessionRef.current !== mySession) return;
      setError(formatQuizError(err, "answer"));
      setPhase("answering");
    } finally {
      if (sessionRef.current === mySession) {
        inFlightRef.current = false;
      }
    }
  };

  // Next Question
  const handleNextQuestion = () => {
    if (!session) return;
    if (session.index < session.quiz.questions.length - 1) {
      setSession((prev) => (prev ? { ...prev, index: prev.index + 1 } : null));
      setCurrentDraft("");
      setError(null);
      setPhase("answering");
      startTimeRef.current = performance.now();
    }
  };

  // See Results / Finish Quiz (C5)
  const handleSeeResults = async () => {
    if (inFlightRef.current || !session) return;
    const returnPhase: "feedback" | "answering" =
      phase === "answering" ? "answering" : "feedback";

    const mySession = sessionRef.current;
    inFlightRef.current = true;
    setPhase("finishing");
    setError(null);
    setSession((prev) => (prev ? { ...prev, returnPhase } : null));

    try {
      const summary = await finishQuiz(notebookId, session.quiz.id);
      if (sessionRef.current !== mySession) return;
      setSession((prev) => (prev ? { ...prev, summary } : null));
      setPhase("review");
    } catch (err) {
      if (sessionRef.current !== mySession) return;
      setError(formatQuizError(err, "finish"));
      setPhase(returnPhase);
    } finally {
      if (sessionRef.current === mySession) {
        inFlightRef.current = false;
      }
    }
  };

  // Quit Quiz handler (C4: sessionRef increment, inFlightRef clear, keep topics selected)
  const handleQuitQuiz = () => {
    sessionRef.current += 1;
    inFlightRef.current = false;
    setAppliedTopicIds(null);
    if (session) {
      setSelectedTopicIds(session.quiz.topic_ids);
    }
    setSession(null);
    setCurrentDraft("");
    setError(null);
    setPhase("start");
  };

  // Replay handlers (C4)
  const handleNewQuizSameTopics = () => {
    setAppliedTopicIds(null);
    if (session) {
      setSelectedTopicIds(session.quiz.topic_ids);
      setCount(session.quiz.questions.length >= 10 ? 10 : 5);
    }
    setSession(null);
    setCurrentDraft("");
    setError(null);
    setPhase("start");
  };

  const handleChooseOtherTopics = () => {
    setAppliedTopicIds(null);
    setSelectedTopicIds([]);
    setCount(5);
    setSession(null);
    setCurrentDraft("");
    setError(null);
    setPhase("start");
  };

  // Render Start / Loading / Queries error states
  if (phase === "start" || phase === "creating") {
    if (topicsQuery.isPending || (hasOpenedQuiz && questionBankQuery.isPending)) {
      return (
        <div className="p-6">
          <p className="text-sm text-muted-foreground">Loading topics…</p>
        </div>
      );
    }

    if (topicsQuery.isError || questionBankQuery.isError) {
      return (
        <div className="p-6">
          <div className="rounded-lg border p-4 space-y-3 bg-muted/20">
            <p className="text-sm text-muted-foreground">
              Couldn&apos;t load the quiz topics.
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                if (topicsQuery.isError) topicsQuery.refetch();
                if (questionBankQuery.isError) questionBankQuery.refetch();
              }}
            >
              Try again
            </Button>
          </div>
        </div>
      );
    }

    // F4: Derive empty topic notice if applied topic has no verified questions
    let emptyTopicNotice: string | null = null;
    if (appliedTopicIds && questionBankQuery.data) {
      for (const tid of appliedTopicIds) {
        const count = servedCountsByTopic.get(tid) ?? 0;
        if (count === 0) {
          const topicName = topicNameMap.get(tid) ?? "This topic";
          emptyTopicNotice = `${topicName} has no quiz questions yet.`;
          break;
        }
      }
    }

    return (
      <div className="p-6">
        <QuizStartForm
          topics={topicsQuery.data?.items ?? []}
          servedCountsByTopic={servedCountsByTopic}
          selectedTopicIds={selectedTopicIds}
          count={count}
          onToggleTopic={handleToggleTopic}
          onCountChange={handleCountChange}
          onStart={handleStartQuiz}
          isCreating={phase === "creating"}
          createError={error?.source === "create" ? error : null}
          onRetryCreate={handleStartQuiz}
          onBackToTopics={handleClearStartError}
          emptyTopicNotice={emptyTopicNotice}
        />
      </div>
    );
  }

  // Render Review
  if (phase === "review" && session?.summary) {
    return (
      <div className="p-6">
        <QuizReview
          summary={session.summary}
          questions={session.quiz.questions}
          results={session.results}
          topicNameMap={topicNameMap}
          onNewQuizSameTopics={handleNewQuizSameTopics}
          onChooseOtherTopics={handleChooseOtherTopics}
        />
      </div>
    );
  }

  // Render Active Question & Feedback (answering, submitting, feedback, finishing)
  if (session) {
    const currentQ = session.quiz.questions[session.index];
    const currentResult = session.results[currentQ?.id];

    // C5: While finishing, render the view of returnPhase with its buttons disabled
    const isFeedbackView =
      phase === "feedback" ||
      (phase === "finishing" && session.returnPhase === "feedback");
    const isFinishing = phase === "finishing";
    const isLocked = isFeedbackView || phase === "submitting" || isFinishing;

    const markers =
      isFeedbackView && currentResult
        ? {
            correctOptionId: currentResult.feedback.correct_option_id,
            chosenOptionId: currentResult.answer,
          }
        : undefined;

    return (
      <div className="p-6 space-y-6">
        {currentQ && (
          <QuizQuestionCard
            question={currentQ}
            questionNumber={session.index + 1}
            totalQuestions={session.quiz.questions.length}
            topicName={topicNameMap.get(currentQ.topic_id) ?? "This topic"}
            currentDraft={currentDraft}
            onDraftChange={setCurrentDraft}
            onSubmit={handleCheckAnswer}
            onQuit={handleQuitQuiz}
            isSubmitting={phase === "submitting" || phase === "finishing"}
            isLocked={isLocked}
            markers={markers}
            submitError={
              phase === "answering" &&
              (error?.source === "answer" || error?.source === "finish")
                ? error
                : null
            }
            onRetry={error?.source === "finish" ? handleSeeResults : handleCheckAnswer}
            onSeeResultsFromFinishedError={handleSeeResults}
            onBackToTopics={handleQuitQuiz}
            headingRef={questionHeadingRef}
          />
        )}

        {isFeedbackView && currentResult && (
          <QuizFeedback
            question={currentQ}
            result={currentResult}
            onNext={
              session.index === session.quiz.questions.length - 1
                ? handleSeeResults
                : handleNextQuestion
            }
            isLastQuestion={session.index === session.quiz.questions.length - 1}
            isFinishing={isFinishing}
            headingRef={feedbackHeadingRef}
            finishError={error?.source === "finish" ? error : null}
            onRetryFinish={handleSeeResults}
            onBackToTopics={handleQuitQuiz}
          />
        )}
      </div>
    );
  }

  return null;
}
