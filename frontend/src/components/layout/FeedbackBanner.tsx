import React from "react";
import { useFeedback } from "../../store";

export const FeedbackBanner: React.FC = () => {
  const feedback = useFeedback();
  const isHidden = !feedback || feedback.hidden;
  const cls =
    feedback?.kind === "error"
      ? "is-error"
      : feedback?.kind === "warn"
        ? "is-warn"
        : "is-ok";

  return (
    <div
      id="feedback-banner"
      className={`feedback-banner${feedback ? ` ${cls}` : ""}`}
      role="status"
      aria-live="polite"
      hidden={isHidden}
    >
      {feedback?.message || ""}
    </div>
  );
};

export default FeedbackBanner;
