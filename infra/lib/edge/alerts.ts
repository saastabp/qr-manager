/**
 * Alerts: tells the owner when scans stop working properly.
 *
 * Two different failures need two alarms. If the function runs but cannot read KVS, it
 * still redirects, so only its log lines show the problem. If the function cannot run at
 * all, it writes no logs and scans get a 503, so only CloudFront's metrics show that.
 * A missing key is not a failure: bots probing paths such as /wp-login cause those.
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

/** One failure is enough: at this scan volume, an outage may produce only a few. */
const FAILURE_THRESHOLD = 1;

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

    // --- KVS failure alarm --------------------------------------------------------
    // A text match rather than a JSON match, because CloudFront prefixes each log line
    // with the request id, so a line is not valid JSON. Matches fallback outcomes,
    // excluding the ordinary miss that logs KeyNotFound.
    const failureFilter = functionLogs.addMetricFilter('KvsFailureFilter', {
      filterPattern: logs.FilterPattern.literal('"\\"outcome\\":\\"fallback\\"" -"KeyNotFound"'),
      metricNamespace: 'QrManager',
      metricName: `${config.envName}-kvs-failures`,
      metricValue: '1',
    });

    const failureAlarm = new cloudwatch.Alarm(this, 'KvsFailureAlarm', {
      alarmName: resourceName(config, 'kvs-failures'),
      alarmDescription: 'The redirect function could not read KVS, so scans are going home',
      metric: failureFilter.metric({ statistic: 'Sum', period: Duration.minutes(5) }),
      threshold: FAILURE_THRESHOLD,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      // No scans in a period is normal, not a problem.
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    failureAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.topic));

    // --- Function failure alarm ---------------------------------------------------
    // CloudFront publishes these metrics itself. FILL treats a period with no data as
    // zero, so one metric being absent does not hide the other.
    const functionMetric = (metricName: string): cloudwatch.Metric =>
      new cloudwatch.Metric({
        namespace: 'AWS/CloudFront',
        metricName,
        dimensionsMap: { FunctionName: resourceName(config, 'redirect'), Region: 'Global' },
        statistic: 'Sum',
        period: Duration.minutes(5),
      });

    const functionAlarm = new cloudwatch.Alarm(this, 'FunctionFailureAlarm', {
      alarmName: resourceName(config, 'function-errors'),
      alarmDescription: 'The redirect function could not run, so scans are getting a 503',
      metric: new cloudwatch.MathExpression({
        expression: 'FILL(errors, 0) + FILL(throttles, 0)',
        usingMetrics: {
          errors: functionMetric('FunctionExecutionErrors'),
          throttles: functionMetric('FunctionThrottles'),
        },
        period: Duration.minutes(5),
      }),
      threshold: FAILURE_THRESHOLD,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    functionAlarm.addAlarmAction(new cloudwatchActions.SnsAction(this.topic));
  }
}