/** Keep rapid automatic edits ordered, including edits queued during a drain. */
export function serialEdits() {
  let tail = Promise.resolve();
  let count = 0;
  return {
    get pending() { return count; },
    enqueue(work: () => Promise<void>) {
      count++;
      const result = tail.then(work).finally(() => { count--; });
      tail = result.catch(() => {});
      return result;
    },
    async drain() { while (count) await tail; },
  };
}
