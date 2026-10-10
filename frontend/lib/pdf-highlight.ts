export type Bbox = [number, number, number, number];

export type CssRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

/**
 * Returns the value as a Bbox only if it is an array of exactly 4 finite numbers
 * with x1 > x0 and y1 > y0; otherwise null.
 */
export function toBbox(value: unknown): Bbox | null {
  if (!Array.isArray(value) || value.length !== 4) {
    return null;
  }
  const [x0, y0, x1, y1] = value;
  if (
    typeof x0 !== "number" ||
    typeof y0 !== "number" ||
    typeof x1 !== "number" ||
    typeof y1 !== "number" ||
    !Number.isFinite(x0) ||
    !Number.isFinite(y0) ||
    !Number.isFinite(x1) ||
    !Number.isFinite(y1)
  ) {
    return null;
  }
  if (x1 <= x0 || y1 <= y0) {
    return null;
  }
  return [x0, y0, x1, y1];
}

/**
 * Scales, expands, and clamps a bounding box in PDF points to percentages of the page box
 * so the highlight scales automatically with zoom and window resizes.
 * Returns null if pageWidthPt or pageHeightPt is not > 0,
 * or if no clamped area remains.
 */
export function bboxToCssRect(
  bbox: Bbox,
  pageWidthPt: number,
  pageHeightPt: number,
  renderedWidthPx?: number,
  outsetPx?: number
): CssRect | null {
  if (
    typeof pageWidthPt !== "number" ||
    typeof pageHeightPt !== "number" ||
    !Number.isFinite(pageWidthPt) ||
    !Number.isFinite(pageHeightPt) ||
    pageWidthPt <= 0 ||
    pageHeightPt <= 0
  ) {
    return null;
  }

  const [x0, y0, x1, y1] = bbox;
  const clampedX0 = Math.max(0, Math.min(pageWidthPt, x0));
  const clampedY0 = Math.max(0, Math.min(pageHeightPt, y0));
  const clampedX1 = Math.max(0, Math.min(pageWidthPt, x1));
  const clampedY1 = Math.max(0, Math.min(pageHeightPt, y1));

  if (clampedX1 <= clampedX0 || clampedY1 <= clampedY0) {
    return null;
  }

  let outsetXPercent = 0;
  let outsetYPercent = 0;
  if (
    typeof renderedWidthPx === "number" &&
    Number.isFinite(renderedWidthPx) &&
    renderedWidthPx > 0
  ) {
    const scale = renderedWidthPx / pageWidthPt;
    const renderedHeightPx = pageHeightPt * scale;
    const safeOutset =
      typeof outsetPx === "number" && Number.isFinite(outsetPx) ? outsetPx : 0;
    outsetXPercent = (safeOutset / renderedWidthPx) * 100;
    outsetYPercent = (safeOutset / renderedHeightPx) * 100;
  }

  const left = Math.max(0, (clampedX0 / pageWidthPt) * 100 - outsetXPercent);
  const top = Math.max(0, (clampedY0 / pageHeightPt) * 100 - outsetYPercent);
  const right = Math.min(100, (clampedX1 / pageWidthPt) * 100 + outsetXPercent);
  const bottom = Math.min(100, (clampedY1 / pageHeightPt) * 100 + outsetYPercent);

  const width = right - left;
  const height = bottom - top;

  if (width <= 0 || height <= 0) {
    return null;
  }

  return {
    left,
    top,
    width,
    height,
  };
}
