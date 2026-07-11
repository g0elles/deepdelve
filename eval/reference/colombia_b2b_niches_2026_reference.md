# GOLD REFERENCE — do not feed to the agent

Reference report for the `colombia_b2b_benchmark` (see `eval/colombia_b2b_benchmark.md`).
Produced independently (June 2026, frontier-model deep research + human curation) and used ONLY
for scoring agent runs against it. Never paste this into an agent prompt — the whole point is to
see what the agent finds on its own.

Key scoring anchors (full report below):

- **5 regulatory finalists**: EUDR traceability cacao/coffee/palm (deadline 30-dec-2026 / jun-2027,
  EU Reg. 2023/1115); SAGRILAFT/SARLAFT compliance (~8,000 obligated firms, Res. 2328/2025 for
  transport); RIPS + glosas for small health providers (Res. 2275/2023, 1884/2024); RNDC freight
  compliance + empty backhaul (Res. 20243040058015/2024); energy-communities software (Ley 1715,
  Decreto 2236/2023, CREG 101072/2025).
- **ML-layer niches**: retail demand forecasting (XGBoost/LightGBM), industrial predictive
  maintenance, network-anomaly cybersecurity for mid-size firms (gap vs. Lumu's IoC-centric model),
  alternative credit scoring, insurance/SOAT fraud, coffee/cacao quality vision.
- **Explicitly saturated/discarded**: last-mile/routing TMS, construction ERP, generic legaltech
  (and fintech is the most crowded funding vertical: 60% of 2025 investment).
- **Hard checkable facts**: Colombia Tech Report 2026 — 2,295 active startups (+9.3%), US$857M
  invested, SaaS #1 by count (27%, 610); cacao exports US$265.1M in 2024 (+106%); coffee record
  US$5,400M (sep-2024→ago-2025); ACHC Study #55 — $25.7 billones hospital receivables, 58% overdue;
  cybersecurity market USD 1.03B (2024) → 1.74B (2029, Mordor); apparel market USD 5.79B (2025).

---

[Full reference report follows — verbatim as provided 2026-07-11]

# Nichos Tecnológicos B2B en Colombia (2026)
## Análisis de mercado: problemas de nicho, gatillos regulatorios y ML de bajo costo marginal

**Objetivo:** Identificar problemas de nicho en Colombia donde la tecnología pueda generar
retornos económicos — priorizando nichos con poca o nula competencia y con un segmento cliente
que demuestre capacidad real de pago — y evaluar su potencial de expansión internacional.

### Los 5 nichos regulatorios finalistas

1. **Trazabilidad EUDR (cacao/café/palma)** — EUDR UE 2023/1115; deadline 30-dic-2026
   (grandes/medianas), jun-2027 (pequeñas). Competencia local muy baja (cacao casi nula: solo
   Raizul "Siembra"; AgroDiligence es gratuito y solo para socios). Exportaciones >US$3.000M/año
   a Europa; cacao US$265,1M en 2024 (+106%). Globales: osapiens (700+ clientes), Satelligence,
   TraceX, Meridia, Coolx (caso Caravela Coffee). Café ya cubierto (SICA/FNC), palma (Fedepalma) —
   el hueco real es cacao. Reto técnico: cacao bajo sombra indistinguible de bosque en Sentinel-2
   (estudio Andes: 94% exactitud discriminando agroforestería).
2. **Compliance SAGRILAFT/SARLAFT automatizado** — ~8.000 empresas sector real (SuperSociedades) +
   transporte (Res. 2328 de 6-mar-2025, plazo 6-nov-2025, ajustada por Res. 16615/2025 y
   4607/2026). Competencia: Isolución, Compliance.com.co, RiskInternational — poca IA/UX moderna.
   Decreto 0368/2026 (Finanzas Abiertas) amplía demanda desde abril 2026.
3. **RIPS + gestión de glosas (prestadores pequeños)** — Res. 2275/2023 y 1884/2024, RIPS JSON
   validado vía MUV atado a FEV-Salud; multas hasta 5.000 SMLMV. +55.000 prestadores, ~78%
   independientes. ACHC Estudio de Cartera #55 (abr-2026): $25,7 billones por cobrar, 58% en mora.
   Competencia grande (Medifolios 900+ IPS, Siesa) pero débil en micro/independiente. HL7 FHIR
   obligatorio desde abr-2026 (Res. 1888/2025).
4. **Cumplimiento RNDC + retornos vacíos en carga** — Res. 20243040058015/2024 (obligatoria
   30-nov-2025), ~40.000 manifiestos/día. Buenaventura: 8.000 viajes generados vs 4.000 atraídos.
   GoCargo, Cárgalo, Drivin parcial. Validación: Convoy, Uber Freight, Loadsmart.
5. **Software comunidades energéticas** — Ley 1715, Decreto 2236/2023, CREG 101072/2025, Decreto
   1403/2024. Meta PND: 20.000 comunidades para 2026; COP $1,7 billones en la estrategia estatal.
   Mercado naciente, competencia muy baja.

### Capa ML (anti-LLM: modelos especializados de costo marginal ~0)

Recomendación consolidada: (1) retail forecasting XGBoost/LightGBM — mercado ropa USD 5,79 mil M
2025, segmento mediano desatendido, encaje .NET/SQL Server excelente; (2) mantenimiento predictivo
industrial (Isolation Forest/LSTM, hasta 40% reducción de costos, proyecto DETECTA); (3)
ciberseguridad de anomalías para medianas — mercado USD 1,03 mil M 2024 → 1,74 mil M 2029 (Mordor),
solo 14% de inversión viene de Mipymes (Frost & Sullivan), gap vs Lumu (IoC-céntrico, $38M
levantados, 881 despliegues, upmarket) y A3Sec (sí tiene UEBA); Circular Externa 007/2018 SFC único
mandato SOC/SIEM explícito; (4) scoring crédito alternativo, fraude SOAT (tesis U. Tadeo), calidad
café/cacao por imagen (93-96% exactitud U. Nacional).

### Descartados por saturación
Última milla / TMS ruteo (Rappi, Drivin, DispatchTrack); ERP construcción (Sinco, Construdata);
legaltech genérico (+100 emprendimientos, Lemontech); fintech (60% de la inversión ya).

### Fricciones operativas clave
Retail: el "data mess" — 80% del valor inicial es el pipeline de limpieza, no el modelo. EUDR: la
"fricción del machete" — captura offline-first o el producto no existe. Ciberseguridad: venta larga
sin incidente — auditoría pasiva gratuita de 7 días como movimiento comercial estándar.
