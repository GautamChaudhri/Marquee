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
