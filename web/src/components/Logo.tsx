type LogoProps = { size?: number; className?: string };

/** The JimmyHQ mark. Colours come from CSS variables so it follows the theme. */
export function Logo({ size = 24, className }: LogoProps) {
  return (
    <svg
      viewBox="0 0 720 720"
      width={size}
      height={size}
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="var(--logo-lobe)"
        d="M133 455H270C320 455 358 480 378 518C345 570 290 600 210 598C165 572 135 520 133 455Z"
      />
      <path
        fill="var(--logo-stem)"
        d="M468 118H598V420C598 560 480 632 340 630C290 629 245 618 210 598C300 600 370 560 392 500C404 470 408 440 408 410V178C408 145 435 118 468 118Z"
      />
    </svg>
  );
}
