# AsthmaAware

Current cloud/backend scope: **real NASA ingestion and storage/delivery contracts,
no ML implementation or fabricated forecasts**. Deployable application entrypoint
is `app.cloud:app`; the worker is `app.worker:app`. Both remain local/un-deployed.
See [cloud plan](GOOGLE_CLOUD_IMPLEMENTATION_PLAN.md),
[deployment gates](GOOGLE_CLOUD_DEPLOYMENT.md), and
[backend API/ML handoff](BACKEND_API_DOCUMENTATION.md).
The earlier prototype described below is preserved historical development work;
its score/sample endpoints are not mounted in the new cloud application.

Development application for NASA pollution forecasts and private daily symptom
logging. Forecast downscaling and environmental scores currently use explicitly
labeled placeholders; they are not trained medical predictions.

- [Verification results and remaining work](VERIFICATION_REPORT.md)
- [Verification plan](VERIFICATION_PLAN.md)
- [Live forecast architecture, setup and limits](backend/docs/live-forecast.md)
- [Backend setup](backend/README.md)
- [Frontend setup](Front-end/README.md)
- [Profiles and symptom API](backend/docs/application-api.md)

No cloud deployment or scheduled acquisition is needed for local development.
Real data requires a manually invoked bounded publisher and real configured
credentials; unconfigured services show errors rather than invented forecasts.
