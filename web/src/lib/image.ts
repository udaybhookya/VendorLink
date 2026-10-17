/** Downscale a photo to at most `max` px on the long side; returns base64 JPEG (no data: prefix). */
export async function downscale(file: File, max = 1024): Promise<{ data_b64: string; content_type: string }> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const url = canvas.toDataURL("image/jpeg", 0.82);
  return { data_b64: url.slice(url.indexOf(",") + 1), content_type: "image/jpeg" };
}
