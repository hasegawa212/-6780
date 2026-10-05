import type {
  AuditLog,
  BudgetService,
  CallRepository,
  CampaignRepository,
  Clock,
  ConsentRepository,
  ContactRepository,
  EventPublisher,
  FollowUpRepository,
  IdGenerator,
  OrganizationRepository,
  OutcomeRepository,
  SafetyControls,
  SuppressionService,
  TelephonyProvider,
  UnitOfWork,
} from "./ports.js";

export interface Deps {
  readonly clock: Clock;
  readonly ids: IdGenerator;
  readonly organizations: OrganizationRepository;
  readonly contacts: ContactRepository;
  readonly campaigns: CampaignRepository;
  readonly calls: CallRepository;
  readonly outcomes: OutcomeRepository;
  readonly followUps: FollowUpRepository;
  readonly suppression: SuppressionService;
  readonly consents: ConsentRepository;
  readonly audit: AuditLog;
  readonly events: EventPublisher;
  readonly uow: UnitOfWork;
  readonly telephony: TelephonyProvider;
  readonly safety: SafetyControls;
  readonly budget: BudgetService;
}

/** API の統一エラー形式 `{"error":{"code":…}}` の code になる値。 */
export interface AppError {
  readonly code: string;
  readonly reasons?: readonly string[];
}
