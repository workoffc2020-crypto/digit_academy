from app.security import auth


def make_user(ctx, admin, username, role):
    auth.create_user(ctx.db, admin, username, "pass1234", role, username.title())
    return auth.login(ctx.db, username, "pass1234")
