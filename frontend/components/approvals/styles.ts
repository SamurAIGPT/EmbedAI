// A field without a width, for one that sets its own (w-36, w-auto): adding a
// width class to FIELD does not work, because w-full can win in the stylesheet.
export const FIELD_BOX =
  "rounded-md border border-zinc-300 bg-transparent px-3 py-1.5 text-sm placeholder:text-zinc-500 dark:border-zinc-700";
export const FIELD = `w-full ${FIELD_BOX}`;
export const BUTTON =
  "cursor-pointer rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900";
export const PRIMARY =
  "cursor-pointer rounded-md bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";
