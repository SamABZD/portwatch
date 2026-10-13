import { mkdir, copyFile } from "node:fs/promises";

// Serve the installed MapLibre module worker explicitly instead of a bundler-relative URL.
// These generated files retain their upstream BSD license and are not hand-edited or committed.
const target = new URL("../public/vendor/maplibre/", import.meta.url);
await mkdir(target, { recursive: true });
for (const name of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  await copyFile(
    new URL(`../node_modules/maplibre-gl/dist/${name}`, import.meta.url),
    new URL(name, target),
  );
}
await copyFile(
  new URL("../node_modules/maplibre-gl/LICENSE.txt", import.meta.url),
  new URL("LICENSE.txt", target),
);
console.log("Prepared local MapLibre module worker and license.");
