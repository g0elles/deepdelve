# Phase 0 reference check (seed 20261127: all comparison parts + 25% of the rest, plus every snippet-only ref)

For each: open the URL, confirm (1) it loads, (2) the quote is on the page verbatim, (3) the reference statement matches the source,
(4) the source class is allowed. Write OK or FIX: <what> after `check:`.

## acad.cmp (comparison, verified_by abstract)
- part: Chile vs Mexico on the size of the effect on sugary-drink purchases, or a reasoned not-comparable statement
- reference: Not directly comparable: Chile's estimate is a measured purchase change (about -24% volume of high-in beverages), while Mexico's peer-reviewed evidence is self-reported purchase reduction or sales of all packaged products combined; different designs and measures. A report that states both figures and says Chile's measured effect is the larger/better-evidenced one, while noting the design difference, also answers.
- url: https://doi.org/10.1371/journal.pmed.1003015
- quote: "Purchases of high-in beverages significantly declined following implementation of Chile's Law of Food Labeling and Advertising"
- check: 

## c2.cmp (comparison, verified_by abstract)
- part: Semaglutide vs naltrexone on strength of evidence for reducing drinking, or a reasoned not-comparable statement
- reference: Naltrexone's evidence is much stronger and established (dozens of RCTs, meta-analyses, approved treatment); semaglutide rests on one small phase 2 trial with mixed outcomes; no head-to-head trial exists, so effect sizes are not directly comparable.
- url: https://doi.org/10.1001/jama.2014.3628
- quote: "Most assessed acamprosate (27 studies, n = 7519), naltrexone (53 studies, n = 9140), or both."
- check: 

## c3.cmp_unemp (comparison, verified_by page)
- part: Iceland vs Ireland on unemployment after the crisis, or a reasoned not-comparable statement
- reference: Ireland's unemployment rose much higher (about double Iceland's peak: ~15% vs ~7.5%) and stayed high longer; Iceland's peaked in 2010 and fell from 2011.
- url: https://fred.stlouisfed.org/data/LRUNTTTTIEA156S
- quote: "| 2012-01-01 | 15.449740 |"
- check: 

## c3.cmp_gdp (comparison, verified_by snippet)
- part: Iceland vs Ireland on GDP growth after the crisis, or a reasoned not-comparable statement
- reference: Both contracted sharply in 2009 by a broadly similar amount (Iceland about -6.5 to -7.7%, Ireland about -5 to -7.6%, depending on source and data vintage) and returned to growth around 2011. Any stated relation (e.g. which fell more, which recovered faster) counts only if backed by figures for both countries in the cited saved source; Irish GDP after 2015 is distorted by multinational accounting (a report noting this is correct).
- url: https://www.rte.ie/news/business/2010/0305/128406-iceland-business
- quote: "GDP decreased by 6.5% in real terms"
- check: 

## cbti.cmp (comparison, verified_by abstract)
- part: CBT-I vs sleep medication on effectiveness, or a reasoned not-comparable statement
- reference: CBT-I is more effective in the long term (remission OR 1.82, 95% CI 1.15-2.87, high certainty; Furukawa et al. 2024) and at least comparable short term (medication may act faster); effects more durable.
- url: https://doi.org/10.1111/pcn.13730
- quote: "CBT-I was more beneficial than pharmacotherapy in the long term (median duration, 24 weeks [range, 12 to 48 weeks]; remission odds ratio, 1.82"
- check: 

## heat.cmp (comparison, verified_by page)
- part: Heat pump vs gas boiler on annual running cost in the UK, or a reasoned not-comparable statement
- reference: Broadly similar at standard capped tariffs: an efficient heat pump (SCOP about 2.9-3.1 or higher) matches or beats a gas boiler; a less efficient one costs somewhat more; heat-pump/time-of-use tariffs make it cheaper.
- url: https://www.which.co.uk/news/article/a-heat-pump-might-be-a-lot-cheaper-than-you-think-heres-how-aSx7P0B2hOHW
- quote: "the running costs for a heat pump can match or beat the costs for a gas boiler"
- check: 

## acad.mexico (figure, verified_by abstract)
- part: Effect of Mexico's 2020 warning labels on purchases of sugary drinks, from peer-reviewed research
- reference: Peer-reviewed evidence is mainly self-reported: in the 2020-2021 International Food Policy Study, 44.8% of adults said the labels made them buy less unhealthy food, with sugary drinks (cola, regular and diet soda) the largest category (about half of adults). A sales-based study (Social Science & Medicine) found reductions in calories (-15%) and added sugars (-25%) across all packaged foods and beverages combined, with no beverage-only figure. Any of these, correctly attributed, answers the part.
- url: https://doi.org/10.1186/s12966-024-01609-3
- quote: "Overall, 44.8% of adults and 38.7% of youth reported buying less of unhealthy food categories due to the implementation of WL, with the largest proportion reporting decreased purchases of cola, regular and diet soda."
- check: 

## cbti.cbt (fact, verified_by abstract)
- part: Effectiveness of CBT for insomnia (CBT-I) per trials or guidelines
- reference: Effective; recommended as initial treatment for chronic insomnia (ACP 2016 guideline: strong recommendation, moderate-quality evidence).
- url: https://doi.org/10.7326/m15-2175
- quote: "ACP recommends that all adult patients receive cognitive behavioral therapy for insomnia (CBT-I) as the initial treatment for chronic insomnia disorder. (Grade: strong recommendation, moderate-quality evidence)."
- check: 

## cbti.meds (fact, verified_by abstract)
- part: Effectiveness of sleep medication per trials
- reference: Medications work in the short term (very low grade evidence that benzodiazepines are more effective than CBT-I short term), with less durable effects; guidelines reserve them as an add-on (weak recommendation, low-quality evidence).
- url: https://doi.org/10.1186/1471-2296-13-40
- quote: "very low grade evidence suggests benzodiazepines are more effective in the short term"
- check: 

## c3.is_unemp (figure, verified_by page)
- part: Iceland's peak unemployment rate after the 2008 crisis
- reference: About 7.5% (annual, 2010, OECD harmonised); monthly/registered series peak near 9%.
- url: https://fred.stlouisfed.org/data/LRUNTTTTISA156S
- quote: "| 2010-01-01 | 7.548411 |"
- check: 

## c2.sema (fact, verified_by abstract)
- part: Whether semaglutide reduces alcohol consumption in people with alcohol use disorder (trial evidence)
- reference: One phase 2 RCT (Hendershot et al. 2025, JAMA Psychiatry, n=48, 9 weeks): semaglutide reduced alcohol consumed in a lab self-administration task (primary) and drinks per drinking day (beta -0.41), but not drinks per calendar day or number of drinking days. Early, small evidence.
- url: https://doi.org/10.1001/jamapsychiatry.2024.4789
- quote: "Semaglutide treatment did not affect average drinks per calendar day or number of drinking days, but significantly reduced drinks per drinking day"
- check: 

## leave.kr (fact, verified_by snippet)
- part: South Korea: minimum days and how leave pay is calculated
- reference: 15 days after one year with at least 80% attendance; 1 day per month worked before that; +1 day per 2 years of service after the first year (from the 3rd year), capped at 25 days; paid at ordinary wages or average wages (Labor Standards Act Art. 60).
- url: https://www.crowe.com/kr/en-us/news/news20180601_en
- quote: "15 days of paid annual leave if the employees fulfilled at least 80% attendance during one (1) year"
- check: 

## heat.annual (figure, verified_by snippet)
- part: An annual running-cost figure for a heat pump and for a gas boiler (stated period)
- reference: Any dated annual figure for both technologies from a cited saved source (e.g. at the July 2025 cap: about £630 gas boiler vs about £735 air-source heat pump for a typical home). Grade by the source-backed rule; numbers vary by cap period.
- url: https://www.granddesignsmagazine.com/advice/how-much-does-a-heat-pump-cost-to-run
- quote: "£630.20"
- check: 
