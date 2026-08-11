import type { ReactNode } from "react";
import Link from "next/link";

export function InformationPage({
  eyebrow,
  title,
  intro,
  children,
}: {
  eyebrow: ReactNode;
  title: ReactNode;
  intro: ReactNode;
  children: ReactNode;
}) {
  return (
    <article className="mx-auto max-w-[860px] px-5 pb-20 pt-32 sm:px-6 md:pb-28 md:pt-40">
      <header className="border-b border-[var(--z-border-strong)] pb-10 md:pb-12">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
          {eyebrow}
        </p>
        <h1 className="mt-5 max-w-[780px] text-[clamp(3rem,8vw,5.5rem)] font-semibold leading-[0.96] tracking-[-0.052em]">
          {title}
        </h1>
        <p className="mt-7 max-w-[62ch] text-lg leading-relaxed text-muted-foreground">
          {intro}
        </p>
      </header>

      <div className="[&>section]:border-b [&>section]:border-border [&>section]:py-9 [&>section:last-child]:border-b-0 [&_a]:inline-flex [&_a]:min-h-10 [&_a]:items-center [&_a]:align-middle [&_a]:text-brand-foreground [&_a]:underline [&_a]:decoration-[color:rgb(243_107_50_/_0.45)] [&_a]:underline-offset-4 [&_a]:transition-colors [&_a:hover]:text-[var(--z-orange-bright)] [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:tracking-[-0.02em] [&_li]:pl-1 [&_p]:mt-4 [&_p]:max-w-[68ch] [&_p]:text-[15px] [&_p]:leading-7 [&_p]:text-muted-foreground [&_ul]:mt-4 [&_ul]:grid [&_ul]:list-disc [&_ul]:gap-2 [&_ul]:pl-5 [&_ul]:text-[15px] [&_ul]:leading-7 [&_ul]:text-muted-foreground">
        {children}
      </div>

      <footer className="mt-7 border-t border-[var(--z-border-strong)] pt-7">
        <Link
          href="/"
          className="inline-flex min-h-10 items-center font-mono text-[11px] font-medium uppercase tracking-[0.11em] text-muted-foreground transition-colors hover:text-foreground"
        >
          ← Back to Zolli Cloud
        </Link>
      </footer>
    </article>
  );
}
