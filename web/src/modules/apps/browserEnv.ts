// The browser's side of two bridge calls: the real `ThumbEnv` (a canvas) and `SaveEnv` (an
// object address and a download link). The rules are in lib/thumbnail.ts and lib/saving.ts,
// tested with fakes; only a real browser can confirm what is here.

import type { SaveEnv } from "./lib/saving.ts";
import { fitWithin } from "./lib/thumbnail.ts";
import type { ThumbEnv } from "./lib/thumbnail.ts";

/** The environment of the real page: createImageBitmap and a canvas. */
export function browserThumbEnv(): ThumbEnv {
  return {
    async render(blob, px, type) {
      if (typeof createImageBitmap !== "function" || typeof document === "undefined") return null;
      const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
      try {
        const { width, height } = fitWithin(bitmap.width, bitmap.height, px);
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        if (!context) return null;
        context.imageSmoothingQuality = "high";
        context.drawImage(bitmap, 0, 0, width, height);
        return await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, 0.85));
      } finally {
        bitmap.close();
      }
    },
  };
}

/** The environment of the real page. */
export function browserSaveEnv(): SaveEnv {
  return {
    makeBlob: (body) => new Blob([body as unknown as Uint8Array<ArrayBuffer>], { type: "application/octet-stream" }),
    createUrl: (blob) => URL.createObjectURL(blob),
    revokeUrl: (url) => URL.revokeObjectURL(url),
    click: (url, name) => {
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.rel = "noopener";
      link.hidden = true;
      document.body.appendChild(link);
      try {
        link.click();
      } finally {
        link.remove();
      }
    },
    later: (work) => {
      setTimeout(work, 10_000);
    },
  };
}
