/**
 * Failure alarms: the settings every QR Manager alarm shares.
 *
 * Every alarm means the same thing, that something failed and the owner should look, so
 * they differ only in what they measure. See docs/DESIGN.md, section 9.
 */
import { Duration } from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { EnvConfig } from './config';

/** How often failures are added up. */
export const ALARM_PERIOD = Duration.minutes(5);

/** One failure is enough: at this scan volume, an outage may produce only a few. */
const FAILURE_THRESHOLD = 1;

/** Turns matching log lines into a metric that counts each line as one failure. */
export function failureFilterProps(
  config: EnvConfig,
  name: string,
  filterPattern: logs.IFilterPattern,
): logs.MetricFilterOptions {
  return {
    filterPattern,
    metricNamespace: 'QrManager',
    metricName: `${config.envName}-${name}`,
    metricValue: '1',
  };
}

/** The metric of a log filter, added up over the alarm period. */
export function failureCount(filter: logs.MetricFilter): cloudwatch.Metric {
  return filter.metric({ statistic: 'Sum', period: ALARM_PERIOD });
}

/** Inputs to an alarm. */
export interface FailureAlarmProps {
  readonly alarmName: string;
  readonly alarmDescription: string;
  readonly metric: cloudwatch.IMetric;
  /** Where the alarm sends its alert. */
  readonly topic: sns.ITopic;
}

/** Create an alarm that fires on one failure and alerts the topic. */
export function addFailureAlarm(
  scope: Construct,
  id: string,
  props: FailureAlarmProps,
): cloudwatch.Alarm {
  const alarm = new cloudwatch.Alarm(scope, id, {
    alarmName: props.alarmName,
    alarmDescription: props.alarmDescription,
    metric: props.metric,
    threshold: FAILURE_THRESHOLD,
    evaluationPeriods: 1,
    comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    // No scans in a period is normal, not a problem.
    treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
  });
  alarm.addAlarmAction(new cloudwatchActions.SnsAction(props.topic));
  return alarm;
}