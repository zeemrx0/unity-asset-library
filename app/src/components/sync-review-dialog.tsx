import { useCallback, useRef, type RefObject } from "react";
import { ArrowRight, X } from "lucide-react";
import {
  AlertDialog,
  AlertDialogBackdrop,
  AlertDialogBody,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  Button,
  Icon,
} from "./ui";
import { formatBytes } from "../format-bytes";

type Props = {
  review: SyncReview;
  applying: boolean;
  error: string;
  finalFocusRef: RefObject<HTMLButtonElement | null>;
  onConfirm: () => void;
  onDiscard: () => void;
  onClose: () => void;
};

// One confirmation for every file change a sync found: old versions to delete
// and archives to move into Store-category folders. Index updates and Store
// matching already happened; nothing here runs until the user confirms.
export function SyncReviewDialog({ review, applying, error, finalFocusRef, onConfirm, onDiscard, onClose }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const setContentRef = useCallback((node: HTMLDivElement | null) => {
    contentRef.current = node;
    if (!node) queueMicrotask(() => { if (!contentRef.current) finalFocusRef.current?.focus(); });
  }, [finalFocusRef]);
  const cleanup = review.cleanup?.preview || {};
  const removals = cleanup.removals || [];
  const moves = review.organize?.preview?.moves || [];
  const deleteBytes = removals.reduce((sum, row) => sum + Number(row.size_bytes || 0), 0);
  return (
    <AlertDialog isOpen onClose={() => { if (!applying) onClose(); }} finalFocusRef={finalFocusRef} initialFocusRef={cancelRef}
      isKeyboardDismissable={!applying} closeOnOverlayClick={false} className="dialog-overlay">
      <AlertDialogBackdrop className="dialog-backdrop" />
      <AlertDialogContent ref={setContentRef} className="action-dialog" aria-labelledby="sync-review-title" aria-describedby="sync-review-description">
        <AlertDialogHeader className="dialog-top">
          <div>
            <div className="eyebrow">CONFIRMATION REQUIRED</div>
            <h2 id="sync-review-title">Review {review.count} change{review.count === 1 ? "" : "s"}</h2>
          </div>
          <Button type="button" className="icon-button" onPress={onClose} isDisabled={applying} aria-label="Close dialog">
            <Icon as={X} className="icon" aria-hidden="true" focusable={false} />
          </Button>
        </AlertDialogHeader>
        <AlertDialogBody>
          <p id="sync-review-description" className="dialog-lead">
            {applying
              ? "Applying the approved changes. This dialog stays open until they finish."
              : "Sync updated the index and Store details. These file changes wait for your confirmation."}
          </p>
          {error && <p className="progress-error" role="alert">{error}</p>}
          <div className="dialog-stats">
            <div><strong className="danger-text">{removals.length}</strong><span>old versions to delete</span></div>
            <div><strong>{formatBytes(deleteBytes)}</strong><span>freed</span></div>
            <div><strong>{moves.length}</strong><span>moves into folders</span></div>
          </div>
          <div className="resync-groups">
            {removals.length > 0 && (
              <section className="change-group" aria-label="Delete old versions">
                <h3>Delete old versions (permanent) <span>{removals.length}</span></h3>
                <p>Each file is an older version of a package you keep in a newer version.</p>
                <ul className="change-list">
                  {(cleanup.families || []).filter((family) => family.removals.length > 0).flatMap((family) => [
                    ...(family.survivor ? [<li key={`keep-${family.asset_key}`}><span><strong className="added-text">Keep</strong> · {family.survivor.path}</span><small>{formatBytes(family.survivor.size_bytes ?? 0)}</small></li>] : []),
                    ...family.removals.map((row) => <li key={row.path}><span><strong className="danger-text">Delete</strong> · {row.path}</span><small>{formatBytes(row.size_bytes ?? 0)}</small></li>),
                  ])}
                </ul>
              </section>
            )}
            {moves.length > 0 && (
              <section className="change-group" aria-label="Move into category folders">
                <h3>Move into category folders <span>{moves.length}</span></h3>
                <p>Files move only; nothing is renamed or unpacked.</p>
                <div className="plan-list">
                  {moves.map((row, index) => (
                    <div key={row.src || index}>
                      <span>{row.src}</span>
                      <span className="plan-arrow"><Icon as={ArrowRight} className="icon" aria-hidden="true" focusable={false} /></span>
                      <span>{row.dst}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}
            {review.unavailable.map((note) => <p className="change-empty" key={note}>{note}</p>)}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter className="dialog-foot">
          <Button type="button" className="button quiet" onPress={onDiscard} isDisabled={applying}>Skip these changes</Button>
          <Button type="button" ref={cancelRef} className="button quiet" onPress={onClose} isDisabled={applying} autoFocus>Later</Button>
          <Button type="button" className="button primary" onPress={onConfirm} isDisabled={applying || review.count === 0}>
            {applying ? "Applying…" : `Apply ${review.count} change${review.count === 1 ? "" : "s"}`}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
