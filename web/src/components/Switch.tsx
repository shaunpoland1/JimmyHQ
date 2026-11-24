type SwitchProps = {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  labelledBy?: string;
  describedBy?: string;
  disabled?: boolean;
};

/** An on/off switch (a button with role="switch"). A disabled one can still be focused, and ignores clicks and keys. */
export function Switch({ id, checked, onChange, labelledBy, describedBy, disabled }: SwitchProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      aria-disabled={disabled ? true : undefined}
      onClick={(event) => {
        // A disabled switch stays focusable so a keyboard user can reach the reason it is off.
        if (disabled) {
          event.preventDefault();
          return;
        }
        onChange(!checked);
      }}
      onKeyDown={(event) => {
        if (disabled && (event.key === " " || event.key === "Enter")) event.preventDefault();
      }}
      className={`relative inline-flex h-5 w-9 flex-none items-center rounded-full border transition-colors ${
        disabled ? "cursor-not-allowed opacity-[0.55] " : ""
      }${
        checked ? "border-accent bg-accent" : "border-border bg-bg"
      }`}
    >
      <span
        aria-hidden="true"
        className={`inline-block size-3.5 rounded-full transition-transform ${
          checked ? "translate-x-[18px] bg-accent-fg" : "translate-x-[3px] bg-text-faint"
        }`}
      />
    </button>
  );
}
