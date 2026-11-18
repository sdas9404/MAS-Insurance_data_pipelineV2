# Known limitations

- Synthetic passenger-vehicle observations are not real actuarial, injury, damage, or claims outcomes. Priors are exploratory and uncalibrated; no human risk classes or premiums are assigned.
- Driver and vehicle capability are paired permanently. Route and density exposure also affect observed behaviour; these effects must not be treated as innate human personality.
- The fixed 7,800-second horizon censors trips. Pending insertions, driver-availability gates, and unfinished journeys are retained. A completed experiment run does not mean all 900 study trips arrived. Actual departures can be delayed relative to the controlled schedule.
- SSM geometry warnings imply incomplete conflict coverage. Code 111 is retained but is not interchangeable with collision-output. The default Krauss collision threshold can detect minGap infringement rather than physical contact; no damage severity is modeled.
- Red-signal entries are matched stop-line crossings bracketed at 0.1 seconds. Legal turn permissions remain unverified; verified_legal_violation is null. Very short internal lanes and phase-boundary crossings may be missed.
- OSM/network speed limits are not legally audited. Reported speeding is only an exceedance of simulator lane metadata.
- Telemetry is sampled every 0.5 seconds; event logic runs every 0.1 seconds. Single-step acceleration/braking threshold excursions count as episodes without a minimum duration filter. Thresholds do not define human unsafe behaviour. Queue maxima are from 10-second snapshots.
- Exposure integrals approximate endpoints. TripInfo distance can include teleport progress. Compare rates with censoring and density in view. Headway/TTC nulls depend on interaction opportunities; configured tau is not observed headway.
- Ten independent route slots per driver are retained across runs, not a spatially continuous itinerary. Ten drivers repeat a template once. There are 90 independent study identities, not 10,800 independent people.
- SUMO rounds per-vehicle speed factors to four decimal places, despite full-precision vType inputs; effective values were audited separately. No pedestrians, cyclists, weather, validated blind spots, demographics, or verified failure-to-yield labels are present.
- Calibration, smoke tests, and engineering attempts are preserved in separate folders and excluded from main observational totals. Group later evaluation splits by driver and assess unseen seeds/routes. See the full report and quality JSON for measured counts and warnings.
