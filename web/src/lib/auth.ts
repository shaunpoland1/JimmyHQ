// Validation for the first-run setup form.

export const MIN_PASSWORD_LENGTH = 10;

export type SetupInput = { name: string; password: string; confirm: string };
export type SetupErrors = { name?: string; password?: string; confirm?: string };

export function validateSetup(input: SetupInput): SetupErrors {
  const errors: SetupErrors = {};
  if (!input.name.trim()) errors.name = "Enter your name.";
  if (input.password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (input.confirm !== input.password) errors.confirm = "The passwords do not match.";
  return errors;
}

export function hasErrors(errors: SetupErrors): boolean {
  return Object.keys(errors).length > 0;
}
