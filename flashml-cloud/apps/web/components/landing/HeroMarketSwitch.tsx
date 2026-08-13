export function HeroMarketSwitch() {
  return (
    <div className="hero-market-switch" aria-label="Two ways to join Zolli">
      <div className="hero-market-role" data-market-role="demand">
        <p className="font-sans text-[15px] font-medium tracking-[-0.01em] text-foreground">
          Need computing power?
        </p>
        <p className="mt-1 font-sans text-[15px] leading-[1.62] tracking-[-0.006em] text-muted-foreground">
          Access more compute at a competitive price.
        </p>
      </div>
      <div className="hero-market-role" data-market-role="supply">
        <p className="font-sans text-[15px] font-medium tracking-[-0.01em] text-foreground">
          Have unused computing power?
        </p>
        <p className="mt-1 font-sans text-[15px] leading-[1.62] tracking-[-0.006em] text-muted-foreground">
          Host it and earn from the work it completes.
        </p>
      </div>
    </div>
  );
}
