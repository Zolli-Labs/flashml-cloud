import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Zolli Cloud",
    short_name: "Zolli",
    description:
      "Fault-tolerant distributed compute for cloud GPUs, home rigs, and spare machines.",
    start_url: "/",
    display: "standalone",
    background_color: "#0B0D0E",
    theme_color: "#0B0D0E",
    icons: [
      {
        src: "/brand/icons/android-chrome-192.png",
        sizes: "192x192",
        type: "image/png",
      },
      {
        src: "/brand/icons/android-chrome-512.png",
        sizes: "512x512",
        type: "image/png",
      },
    ],
  };
}
