import { createContext, useContext } from "react";

export type ToastKind = "info" | "success" | "error";
/** A button on a toast, such as Undo or Try again. Pressing it dismisses the toast. */
export type ToastAction = { label: string; run: () => void };
export type ToastItem = { id: number; kind: ToastKind; message: string; action?: ToastAction };

export type ToastApi = {
  show: (kind: ToastKind, message: string, action?: ToastAction) => void;
  error: (message: string, action?: ToastAction) => void;
  success: (message: string, action?: ToastAction) => void;
};

export const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const toast = useContext(ToastContext);
  if (!toast) throw new Error("useToast must be used inside ToastProvider");
  return toast;
}
