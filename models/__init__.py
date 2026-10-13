"""模型包：from models import Xxx 即可。"""
from models.ai import DuckInput, DuckTurn
from models.auth import (
    AccountDeletion,
    Credentials,
    EmailChangeConfirm,
    EmailUpdate,
    ForgotPassword,
    PasswordChange,
    Registration,
    ResetPassword,
    TrialSignup,
    UsernameUpdate,
)
from models.base import (
    InputModel,
    MistakeText,
    PostBody,
    PostTitle,
    ThinkingText,
    Title,
)
from models.forum import (
    AcceptedComment,
    CommentBody,
    CommentEdit,
    NewComment,
    NewPost,
    PostEdit,
    PostFields,
    PostSearchQuery,
    ReportInput,
)
from models.groups import (
    GoalSetting,
    GroupTodayVisibilityInput,
    JoinGroup,
    NewGroup,
    PublicRankSetting,
    WeeklyGoalInput,
)
from models.misc import PushSettingsInput
from models.payments import (
    ManualGrant,
    ManualPaymentSettings,
    NewManualClaim,
    NewOrder,
    NewRedeemCodes,
    RedeemInput,
    RejectManualClaim,
)
from models.problems import (
    MistakeEdit,
    MistakeReasonInput,
    MistakeTags,
    NewProblem,
    ProblemEdit,
    ProblemFields,
)
from models.review import (
    ReviewInput,
    RvbSettingsInput,
    RvbSnoozeInput,
    RvbVersionInput,
    ScratchPut,
    VariantResult,
)

__all__ = [
    "AcceptedComment", "AccountDeletion", "CommentBody", "CommentEdit", "Credentials",
    "DuckInput", "DuckTurn", "EmailChangeConfirm", "EmailUpdate", "ForgotPassword",
    "GoalSetting", "GroupTodayVisibilityInput", "InputModel", "JoinGroup", "ManualGrant",
    "ManualPaymentSettings", "MistakeEdit", "MistakeReasonInput", "MistakeTags",
    "MistakeText", "NewComment", "NewGroup", "NewManualClaim", "NewOrder", "NewPost",
    "NewProblem", "NewRedeemCodes", "PasswordChange", "PostBody", "PostEdit",
    "PostFields", "PostSearchQuery", "PostTitle", "ProblemEdit", "ProblemFields",
    "PublicRankSetting", "PushSettingsInput", "RedeemInput", "Registration",
    "RejectManualClaim", "ReportInput", "ResetPassword", "ReviewInput",
    "RvbSettingsInput", "RvbSnoozeInput", "RvbVersionInput", "ScratchPut",
    "ThinkingText", "Title", "TrialSignup", "UsernameUpdate", "VariantResult",
    "WeeklyGoalInput",
]
