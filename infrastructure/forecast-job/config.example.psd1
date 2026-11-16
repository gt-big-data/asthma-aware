@{
    # Copy to config.local.psd1. Project and region MUST be chosen explicitly.
    ProjectId = 'REPLACE_WITH_PROJECT_ID'
    Region = 'REPLACE_WITH_REGION'
    Repository = 'asthma-forecast-job'
    JobName = 'asthma-forecast-sample'
    SchedulerName = 'asthma-forecast-daily'
    RuntimeAccount = 'asthma-forecast-runtime'
    SchedulerAccount = 'asthma-forecast-scheduler'
    ImageTag = 'sample-v1'
    # Proposed default only: daily 06:00 in the configured timezone.
    Schedule = '0 6 * * *'
    TimeZone = 'America/New_York'
}
