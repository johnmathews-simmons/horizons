export type ChangeType = 'ADDED' | 'REMOVED' | 'MODIFIED' | 'MOVED'

export interface ChangeColor {
  /** Tailwind classes for the bordered clause box in the diff view. */
  box: string
  /** Tailwind classes for the small corner pill that labels the change. */
  pill: string
  /** Human-readable label (matches the enum casing). */
  label: ChangeType
}

export const CHANGE_COLORS: Record<ChangeType, ChangeColor> = {
  ADDED: {
    box: 'rounded-md bg-green-50 ring-2 ring-green-400 p-3',
    pill: 'bg-green-100 text-green-800 ring-green-300',
    label: 'ADDED',
  },
  REMOVED: {
    box: 'rounded-md bg-red-50 ring-2 ring-red-400 p-3',
    pill: 'bg-red-100 text-red-800 ring-red-300',
    label: 'REMOVED',
  },
  MODIFIED: {
    box: 'rounded-md bg-amber-50 ring-2 ring-amber-400 p-3',
    pill: 'bg-amber-100 text-amber-800 ring-amber-300',
    label: 'MODIFIED',
  },
  MOVED: {
    box: 'rounded-md bg-blue-50 ring-2 ring-blue-400 p-3',
    pill: 'bg-blue-100 text-blue-800 ring-blue-300',
    label: 'MOVED',
  },
}
