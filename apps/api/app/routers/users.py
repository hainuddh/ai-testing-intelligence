from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.cache import delete_prefix_sync
from app.dependencies import AdminUser, DbSession
from app.models import Source, User
from app.schemas import AdminUserResponse, UserCreate, UserListResponse, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def active_admin_count(db: DbSession, *, lock: bool = False) -> int:
    if lock and db.get_bind().dialect.name == "postgresql":
        list(
            db.scalars(
                select(User).where(User.role == "admin", User.is_active).with_for_update()
            )
        )
    return (
        db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.is_active)
        )
        or 0
    )


@router.get("", response_model=UserListResponse)
def list_users(
    db: DbSession,
    _admin: AdminUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> UserListResponse:
    total = db.scalar(select(func.count()).select_from(User)) or 0
    items = list(
        db.scalars(select(User).order_by(User.created_at.desc()).offset(offset).limit(limit))
    )
    return UserListResponse(items=items, total=total)


@router.post("", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: DbSession, _admin: AdminUser) -> User:
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already exists"
        ) from exc
    db.refresh(user)
    delete_prefix_sync("database:status")
    return user


@router.patch("/{user_id}", response_model=AdminUserResponse)
def update_user(user_id: int, payload: UserUpdate, db: DbSession, admin: AdminUser) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    changes = payload.model_dump(exclude_unset=True)
    required_changes = {"username", "role", "is_active"} & changes.keys()
    if any(changes.get(field) is None for field in required_changes):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Username, role, and active status cannot be null",
        )
    if user.id == admin.id and changes.get("is_active") is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your current account",
        )
    removes_active_admin = user.role == "admin" and user.is_active and (
        changes.get("role", "admin") != "admin" or changes.get("is_active") is False
    )
    if removes_active_admin and active_admin_count(db, lock=True) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove the last active admin",
        )
    password = changes.pop("password", None)
    if password is not None:
        user.password_hash = hash_password(password)
    for field, value in changes.items():
        setattr(user, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already exists"
        ) from exc
    db.refresh(user)
    delete_prefix_sync("database:status")
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: DbSession, admin: AdminUser) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your current account",
        )
    if user.role == "admin" and user.is_active and active_admin_count(db, lock=True) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete the last active admin",
        )
    if db.scalar(select(Source.id).where(Source.created_by == user.id).limit(1)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a user that owns sources",
        )
    db.delete(user)
    db.commit()
    delete_prefix_sync("database:status")
