/**
 * Alerts: tells the owner when scans are not resolving.
 *
 * The redirect function turns every failure into a redirect, so a function-errors alarm
 * would never fire. This counts "fallback" outcomes in the function's log lines instead.
 * See docs/DESIGN.md, section 9.
 */
import { Duration } from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { EnvConfig, resourceName } from '../config';

/** Fallbacks in one period that trigger the alarm. Tune this if bots trip it. */
const FALLBACK_THRESHOLD = 5;

/** Inputs to this construct. */
export interface AlertsProps {
  readonly config: EnvConfig;
  /** The redirect function's log group. */
  readonly functionLogs: logs.LogGroup;
}

export class Alerts extends Construct {
  // Public because the Api stack sends its own alarm here. For operational alerts only.
  readonly topic: sns.Topic;

  constructor(scope: Construct, id: string, props: AlertsProps) {
    super(scope, id);
    const { config, functionLogs } = props;

    // --- Alert topic --------------------------------------------------------------
    this.topic = new sns.Topic(this, 'Topic', {
      topicName: resourceName(config, 'alerts'),
    });

    // AWS emails a confirmation link first; alerts only arrive after it is clicked.
    if (config.alarmEmail) {
      this.topic.addSubscription(new subscriptions.EmailSubscription(config.alarmEmail));
    }

    // --- Fallback alarm -----------------------------------------------------------
    // A text match rather than a JSON match, because CloudFront may add its own prefix
    // to each log line, which would stop the line parsing as JSON.
    const fallbackFilter = functionLogs.addMetricFilter('FallbackFilter', {
      filterPattern: logs.FilterPattern.literal('"\\"outcome\\":\\"fallback\\""'),
      metricNamespace: 'QrManager',
      metricName: `${config.envName}-fallbacks`,
      metricValue: '1',
    });

    const fallbackAlarm = new cloudwatch.Alarm(this, 'FallbackAlarm', {
      alarmName: resourceName(config, 'fallbacks'),
      alarmDescription: 'Well-formed codes are not resolving, so scans are going home',
      metric: fallbackFilter.metric({ statistic: 'Sum', period: Duration.minutes(5) }),
      threshold: FALLBACK_THRESHOLD,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      // No scans in a period is normal, not a problem.
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    fallbackAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.topic));
  }
}