import { useState } from "react";
import { ThumbsDown, ThumbsUp, Check } from "lucide-react";
import { useDocFeedback } from "@/hooks/useHubInsights";
import { cn } from "@/lib/utils";

interface Props {
  projectId?: string;
  docId?: string;
}

/**
 * Reader feedback footer (architecture §8.9.9). A thumbs-up/down on the current doc;
 * a down-vote opens an optional comment box. Persisted to doc_feedback for the Hub.
 */
export function DocFeedback({ projectId, docId }: Props) {
  const fb = useDocFeedback(projectId, docId);
  const [rating, setRating] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);

  if (!projectId || !docId) return null;

  const vote = (r: number) => {
    setRating(r);
    if (r >= 4) {
      fb.mutate({ rating: r }, { onSuccess: () => setSent(true) });
    }
  };

  const sendWithComment = () => {
    fb.mutate({ rating: rating ?? 1, comment: comment.trim() || undefined }, { onSuccess: () => setSent(true) });
  };

  if (sent) {
    return (
      <div className="mt-8 flex items-center gap-1.5 border-t pt-4 text-xs text-green-700">
        <Check className="h-3.5 w-3.5" /> Thanks for the feedback.
      </div>
    );
  }

  return (
    <div className="mt-8 border-t pt-4">
      <div className="flex items-center gap-3">
        <span className="text-xs text-muted">Was this document helpful?</span>
        <button
          onClick={() => vote(5)}
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-md border hover:bg-surface-2",
            rating === 5 && "border-green-400 bg-green-50 text-green-700",
          )}
          title="Helpful"
        >
          <ThumbsUp className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => vote(1)}
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-md border hover:bg-surface-2",
            rating === 1 && "border-red-400 bg-red-50 text-red-700",
          )}
          title="Not helpful"
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </button>
      </div>

      {rating === 1 && (
        <div className="mt-2 flex items-end gap-2">
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
            placeholder="What was missing or wrong? (optional)"
            className="flex-1 resize-none rounded-md border bg-background px-2.5 py-1.5 text-xs outline-none focus:border-primary"
          />
          <button
            onClick={sendWithComment}
            className="rounded-md bg-primary px-2.5 py-1.5 text-xs text-primary-foreground hover:bg-primary/90"
          >
            Send
          </button>
        </div>
      )}
    </div>
  );
}
