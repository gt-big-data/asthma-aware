# AsthmaAware

AsthmaAware helps people with asthma plan outdoor activities by combining air-quality forecasts with daily symptom reports to provide increasingly personalized estimates of respiratory risk. An interactive map shows how conditions vary by location and time, while a dashboard highlights upcoming conditions and lower-risk periods for being outdoors.

**[Try the deployed app](https://asthmaaware-web-243825508967.us-east1.run.app/dashboard)**

## Features

- Hourly forecasts, a four-day risk overview, pollutant contributors, and seven-day environmental trends.
- Interactive maps with general risk, personalized risk, and environmental layers, plus location and grid-cell inspection.
- Account-based daily symptom logging and saved Georgia cities, with personalization learned from completed symptom reports.

## Technical overview

- **Frontend:** React Native and TypeScript with Expo Router, supporting web and mobile. Maps use MapLibre GL JS through a web iframe or native WebView.
- **Backend:** Python/FastAPI APIs with Supabase Auth and PostgreSQL for private profiles, symptom logs, and personalization state.
- **Data and models:** NASA GEOS-CF forecasts and historical analysis feed a four-pollutant risk model. Experimental ozone downscaling uses a residual U-Net; Bayesian regression learns individual symptom-based adjustments.
- **Cloud:** Google Cloud Run hosts the web app, API, and processing jobs. Cloud Scheduler coordinates forecast checks, and private Cloud Storage holds versioned forecast artifacts.

This is a development deployment with experimental models. Scores indicate relative modeled conditions, not the probability of an asthma attack. Forecast coverage currently includes part of Georgia.

For development details, see the [backend](backend/README.md), [frontend](Front-end/README.md), and [ML](ml-work/README.md) documentation.
