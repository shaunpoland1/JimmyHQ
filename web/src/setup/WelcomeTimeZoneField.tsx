import { useId } from "react";
import { TimeZoneControl } from "../components/TimeZoneControl";

type WelcomeTimeZoneFieldProps = {
  value: string;
  onChange: (name: string) => void;
  error?: string;
};

/** The welcome step's time zone: the same picker Settings uses, under its own label. */
export function WelcomeTimeZoneField({ value, onChange, error }: WelcomeTimeZoneFieldProps) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span id={`${id}-label`} className="text-xs font-medium text-text">
        Time zone
      </span>
      <TimeZoneControl id={id} value={value} onSave={onChange} describedBy={error ? `${id}-error` : undefined} />
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
