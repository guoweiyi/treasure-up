export type CommentSeekScope = { request: number; partId: string; identity: number };
export type CommentSeekIntent = CommentSeekScope & { seconds: number; play: boolean };

/** One user seek survives loading, but never a different source or login session. */
export function createCommentSeekQueue() {
  let pending: CommentSeekIntent | null = null;
  return {
    get pending() {
      return pending !== null;
    },
    clear() {
      pending = null;
    },
    enqueue(scope: CommentSeekScope, seconds: number, play: boolean, duration: number) {
      if (
        !Number.isFinite(seconds) ||
        seconds < 0 ||
        !Number.isFinite(duration) ||
        duration <= seconds
      )
        return false;
      pending = { ...scope, seconds, play };
      return true;
    },
    consume(scope: CommentSeekScope, duration: number) {
      const value = pending;
      pending = null;
      if (
        !value ||
        value.request !== scope.request ||
        value.partId !== scope.partId ||
        value.identity !== scope.identity ||
        !Number.isFinite(duration) ||
        value.seconds >= duration
      )
        return null;
      return value;
    },
  };
}
