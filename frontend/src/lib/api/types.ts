/** Names for the response shapes the pages use, taken from the generated types. Nothing here
 *  is written by hand: each is an alias of a schema in `frontend/openapi.json`. */
import type { components } from './generated/schema';

type Schemas = components['schemas'];

export type Movie = Schemas['MovieItem'];
export type MovieList = Schemas['MovieList'];
export type Poster = Schemas['Poster'];
export type Job = Schemas['JobItem'];
export type JobList = Schemas['JobList'];
export type JobState = Job['state'];
export type JobEvent = Schemas['JobEventItem'];
export type JobEventFeed = Schemas['JobEventFeed'];
export type WorkerInfo = Schemas['WorkerInfo'];
export type RunItem = Schemas['RunItem'];
export type RunDetail = Schemas['RunDetail'];
export type Candidate = Schemas['CandidateItem'];
export type Deployment = Schemas['DeploymentItem'];
export type DeploymentList = Schemas['DeploymentList'];
export type FeedbackRequest = Schemas['FeedbackRequest'];
export type FeedbackResult = Schemas['FeedbackResult'];
export type FeedbackEvent = Schemas['EventItem'];
export type FeedbackListItem = Schemas['FeedbackListItem'];
export type FeedbackList = Schemas['FeedbackList'];
export type Exemplar = Schemas['ExemplarItem'];
export type ExemplarList = Schemas['ExemplarList'];
export type ExemplarChanges = Schemas['ExemplarChanges'];
export type PendingReview = Schemas['PendingItem'];
export type TasteStatus = Schemas['TasteStatus'];
export type Tally = Schemas['TallyItem'];
export type SystemInfo = Schemas['SystemInfo'];
export type Residual = Schemas['ResidualItem'];
export type Evaluation = Schemas['EvaluationItem'];
export type EvaluationList = Schemas['EvaluationList'];
export type EvaluationTally = Schemas['EvaluationTally'];
export type EvaluationResult = Schemas['EvaluationResultItem'];
export type EvaluationResultList = Schemas['EvaluationResultList'];
export type EvaluationQueued = Schemas['EvaluationQueued'];
