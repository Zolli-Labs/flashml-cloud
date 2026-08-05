import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ZolliAI Cloud",
    short_name: "ZolliAI",
    description:
      "A resilient distributed compute crew for laptops, GPU rigs, and cloud instances.",
    start_url: "/",
    display: "standalone",
    background_color: "#FFFDF3",
    theme_color: "#FF7427",
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
