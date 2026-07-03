# This module implements a small, reusable IMAP + SMTP transport wrapper for
# services that need to *listen to* and *reply from* a real email mailbox. It is
# the email analog of `services/lib/lifx.py` / `services/lib/govee.py`: a single
# `Config` subclass plus a client class that concentrates all of the fiddly
# protocol mechanics (connect, authenticate, IMAP IDLE, fetch-without-marking-
# seen, threaded reply construction, SMTP send, and provider-correct permanent
# deletion) behind one clean, easily-mockable seam.
#
# The first intended consumer is the `mailman` service (see architecture report
# `564fcf5155c4d1fc` and its addendum `e57690255b13b68c`), but nothing here is
# mailman-specific: point the config at any IMAP/SMTP provider and it works.
# Gmail is the first target, so the defaults are Gmail-friendly, but every value
# is overridable for provider portability.
#
# Dependency choice: this wrapper is deliberately implemented with the Python
# standard library only (`imaplib`, `smtplib`, `email`). No third-party package
# (e.g. `imap-tools`) is required, so nothing new is installed into any service
# venv via `scripts/run-service.sh` (which always installs
# `services/lib/requirements.txt` for every service). The IMAP IDLE loop is
# hand-rolled on top of `imaplib` (which has no first-class IDLE helper) and is
# fully unit-tested against mocks.
#
# Naming note: this file is intentionally named `email_client.py` (NOT
# `email.py`) so that it can never shadow the Python standard-library `email`
# package that this very module imports for MIME building and parsing.
#
#   Connor Shugg

# Imports
import os
import sys
import ssl
import time
import select
import imaplib
import smtplib
import contextlib
import email
import email.policy
import email.utils
import email.header
from email.message import EmailMessage

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField


# =============================== Constants ================================= #
# Refresh/renew the IMAP IDLE connection this many seconds after it was opened,
# so we proactively re-issue IDLE before the server (RFC 2177 recommends a 29
# minute ceiling) silently drops it. 1740s == 29 minutes.
EMAIL_DEFAULT_IDLE_REFRESH_INTERVAL = 1740

# Delete-mode identifiers. `delete()` uses these to decide how to *permanently*
# remove a processed message (see `EmailClient.delete`).
#   * gmail_trash_expunge: Gmail-correct true delete -- copy the message to
#     "[Gmail]/Trash", remove it from the source mailbox, then expunge it out of
#     Trash so it does not merely get archived.
#   * expunge: RFC-standard delete -- flag `\Deleted` in place and EXPUNGE. This
#     is correct for non-Gmail / standards-compliant IMAP servers.
EMAIL_DELETE_MODE_GMAIL = "gmail_trash_expunge"
EMAIL_DELETE_MODE_EXPUNGE = "expunge"
EMAIL_DELETE_MODES = [EMAIL_DELETE_MODE_GMAIL, EMAIL_DELETE_MODE_EXPUNGE]


# ============================== Error Class ================================ #
class EmailClientError(Exception):
    """Raised when an IMAP/SMTP transport operation cannot be completed
    successfully (e.g. a failed connection, an authentication rejection, a
    non-OK IMAP response, or an SMTP send failure). Mirrors the dedicated error
    types used by the sibling wrappers (`GoveeError`, `LIFXError`).

    Care is taken throughout this module to never place the account password (an
    app password / secret) into an `EmailClientError` message or any log line.
    """
    pass


class EmailConnectionError(EmailClientError):
    """A specialization of `EmailClientError` raised when a live IMAP/SMTP
    connection is dropped or misbehaves at the transport level MID-COMMAND --
    e.g. the socket returns EOF (Gmail closing an idle connection after ~30
    min), a connection reset, an SSL error, or an `imaplib` abort.

    This is distinct from a *logical* protocol failure (a non-OK IMAP response)
    or a genuinely-unfetchable message: those keep raising the plain
    `EmailClientError`. Callers (e.g. mailman's worker) catch this narrower type
    to decide that the correct recovery is to RECONNECT and retry, rather than
    to give up on / re-enqueue the message forever.

    As with `EmailClientError`, the account password is never placed into the
    message.
    """
    pass


# Transport-level exceptions raised by the underlying imaplib/socket/ssl layer
# when a live IMAP connection is dropped or misbehaves mid-command. Mirrors the
# set already handled by `idle_wait` (plus `ssl.SSLError`). These are normalized
# into `EmailConnectionError` so callers can catch one type and reconnect.
IMAP_CONNECTION_ERRORS = (
    OSError, ssl.SSLError, imaplib.IMAP4.abort, imaplib.IMAP4.error,
)

# The analogous transport-level exceptions for the SMTP send path.
SMTP_CONNECTION_ERRORS = (
    OSError, ssl.SSLError,
    smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
)


# =============================== Config Class ============================== #
class EmailClientConfig(Config):
    """Configuration for the `EmailClient` IMAP/SMTP transport wrapper.

    The IMAP and SMTP blocks are kept in one config object because a single
    account is used for both reading (IMAP) and sending (SMTP). Defaults target
    Gmail but are fully overridable so the same wrapper can drive any provider.

    Fields:
      imap_host              IMAP server hostname (e.g. "imap.gmail.com").
      imap_port              IMAP port. 993 for implicit TLS (IMAPS).
      imap_ssl               If true, connect with IMAP4_SSL (implicit TLS);
                             otherwise connect plaintext then issue STARTTLS.
      smtp_host              SMTP server hostname (e.g. "smtp.gmail.com").
      smtp_port              SMTP port. 587 for STARTTLS, 465 for implicit SSL.
      smtp_ssl               If true, connect with SMTP_SSL (implicit TLS, 465);
                             otherwise connect then issue STARTTLS (587).
      username               Account login. Also used as the reply `From:`
                             address unless overridden by `from_address`.
      password               The account's app password. THIS IS A SECRET: it is
                             read from config only and is never logged.
      from_address           Optional explicit `From:` address for outgoing
                             replies. Defaults to `username` when unset.
      from_name              Optional display name for the reply `From:` header
                             (e.g. "DImROD"). When set, replies use
                             "From Name <address>".
      mailbox                The IMAP folder to select/watch. Defaults to INBOX.
      imap_timeout           Socket timeout (seconds) for IMAP operations.
      smtp_timeout           Socket timeout (seconds) for SMTP operations.
      idle_refresh_interval  Seconds after which an open IDLE connection is
                             proactively reconnected (~29 min) so it is refreshed
                             before the server drops it.
      delete_mode            How `delete()` permanently removes a message. One of
                             EMAIL_DELETE_MODES. Gmail default is
                             "gmail_trash_expunge".
      gmail_trash_folder     Trash folder used by the gmail_trash_expunge mode.
    """
    def __init__(self):
        super().__init__()
        self.fields = [
            # --- IMAP connection ---
            ConfigField("imap_host",   [str],  required=True),
            ConfigField("imap_port",   [int],  required=False, default=993),
            ConfigField("imap_ssl",    [bool], required=False, default=True),
            # --- SMTP connection ---
            ConfigField("smtp_host",   [str],  required=True),
            ConfigField("smtp_port",   [int],  required=False, default=587),
            ConfigField("smtp_ssl",    [bool], required=False, default=False),
            # --- Credentials ---
            ConfigField("username",    [str],  required=True),
            ConfigField("password",    [str],  required=True),  # SECRET: never log
            # --- Reply identity ---
            ConfigField("from_address", [str], required=False, default=None),
            ConfigField("from_name",    [str], required=False, default=None),
            # --- Behavior / tuning ---
            ConfigField("mailbox",      [str], required=False, default="INBOX"),
            ConfigField("imap_timeout", [int], required=False, default=30),
            ConfigField("smtp_timeout", [int], required=False, default=30),
            ConfigField("idle_refresh_interval", [int], required=False,
                        default=EMAIL_DEFAULT_IDLE_REFRESH_INTERVAL),
            ConfigField("delete_mode",  [str], required=False,
                        default=EMAIL_DELETE_MODE_GMAIL),
            ConfigField("gmail_trash_folder", [str], required=False,
                        default="[Gmail]/Trash"),
        ]

    def post_parse_init(self):
        """Validates config values after parsing. Ensures `delete_mode` is one of
        the supported strategies so a typo fails loudly at startup rather than
        silently leaving mail undeleted.
        """
        if self.delete_mode not in EMAIL_DELETE_MODES:
            raise EmailClientError(
                "delete_mode must be one of %s (got \"%s\")" %
                (EMAIL_DELETE_MODES, self.delete_mode)
            )


# ============================ Parsed Email ================================= #
class ParsedEmail:
    """A lightweight, convenient view over a fetched message.

    Wraps the underlying stdlib `email.message.EmailMessage` (accessible via
    `.message`) and eagerly extracts the fields callers most commonly need:
    the sender address, subject, threading identifiers, and a decoded plain-text
    body. Keeping the raw `EmailMessage` around means `build_reply` (and any
    caller) still has full access to every header when needed.
    """
    def __init__(self, uid, message: EmailMessage):
        """Constructor. Takes the message's UID (str) and the parsed
        `EmailMessage`, and derives the commonly-used fields from it.
        """
        self.uid = str(uid)
        self.message = message

        # Decode and normalize the From header down to a bare address for
        # allowlist comparison, while also keeping the raw header value.
        self.from_raw = _decode_header_value(message.get("From"))
        self.from_address = email.utils.parseaddr(self.from_raw)[1].strip().lower()

        # Decode the (possibly MIME-encoded) subject. A missing subject is an
        # empty string so downstream reply logic can turn it into "Re:".
        self.subject = _decode_header_value(message.get("Subject"))

        # Threading identifiers, kept verbatim (already ASCII per RFC 5322).
        self.message_id = _clean_header(message.get("Message-ID"))
        self.in_reply_to = _clean_header(message.get("In-Reply-To"))
        self.references = _clean_header(message.get("References"))

        # The decoded plain-text body (HTML is used only as a last resort).
        self.body_text = _extract_plain_text(message)

    def __str__(self):
        return "ParsedEmail(uid=%s, from=%s, subject=%r)" % \
               (self.uid, self.from_address, self.subject)


# ============================== Email Client =============================== #
class EmailClient:
    """The IMAP/SMTP transport wrapper.

    Owns one IMAP connection (for reading/searching/IDLE/delete) and one SMTP
    connection (for sending). All network mechanics live here so consumers work
    against a small, mockable API: `connect`/`disconnect`, `idle_wait`,
    `search_unseen`, `fetch`, `mark_seen`, `build_reply`, `send`, and `delete`.

    Thread-safety note: a single `EmailClient` instance is NOT internally locked.
    The intended usage (mirroring the mailman architecture) is one client for the
    IMAP IDLE listener and separate handling for sends; callers that share one
    instance across threads should provide their own synchronization.
    """
    def __init__(self, config: EmailClientConfig, log=None):
        """Constructor. Takes a parsed `EmailClientConfig` and an optional
        `lib.log.Log` (or any object exposing `write(str)`), used for non-secret
        diagnostic logging. No connections are opened until `connect()`.
        """
        self.config = config
        self.log = log

        # Live connection handles (None until connect()).
        self._imap = None
        self._smtp = None

        # The currently-selected IMAP mailbox and the monotonic timestamp of the
        # last successful IMAP connect (used to schedule IDLE refreshes).
        self._mailbox = config.mailbox
        self._imap_connect_time = None

    # ------------------------------ Logging -------------------------------- #
    def _log_write(self, msg: str):
        """Writes a diagnostic line to the configured log, if any. Callers must
        never pass the account password to this function.
        """
        if self.log is not None:
            self.log.write(msg)

    # --------------------------- Connect / Auth ---------------------------- #
    def connect(self):
        """Establishes and authenticates BOTH the IMAP and SMTP connections.
        Raises `EmailClientError` (without ever exposing the password) if either
        connection or login fails.
        """
        self._connect_imap()
        self._connect_smtp()

    def _connect_imap(self):
        """Opens and authenticates the IMAP connection, then selects the
        configured mailbox. Raises `EmailClientError` on any failure.
        """
        cfg = self.config
        try:
            if cfg.imap_ssl:
                imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port,
                                         timeout=cfg.imap_timeout)
            else:
                imap = imaplib.IMAP4(cfg.imap_host, cfg.imap_port,
                                     timeout=cfg.imap_timeout)
                imap.starttls()
            imap.login(cfg.username, cfg.password)
            typ, _ = imap.select(self._mailbox)
            if typ != "OK":
                raise EmailClientError(
                    "failed to select IMAP mailbox \"%s\"" % self._mailbox
                )
        except EmailClientError:
            raise
        except Exception as e:
            # Deliberately do NOT include the password (or the exception's raw
            # repr, which some libs stuff credentials into) verbatim.
            raise EmailClientError(
                "IMAP connect/login failed for %s@%s:%d: %s" %
                (cfg.username, cfg.imap_host, cfg.imap_port, e)
            )

        self._imap = imap
        self._imap_connect_time = time.monotonic()
        self._log_write("IMAP connected to %s:%d as %s (mailbox=%s)." %
                        (cfg.imap_host, cfg.imap_port, cfg.username, self._mailbox))

    def _connect_smtp(self):
        """Opens and authenticates the SMTP connection. Raises
        `EmailClientError` on any failure.
        """
        cfg = self.config
        try:
            if cfg.smtp_ssl:
                smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port,
                                        timeout=cfg.smtp_timeout)
            else:
                smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port,
                                    timeout=cfg.smtp_timeout)
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            smtp.login(cfg.username, cfg.password)
        except Exception as e:
            raise EmailClientError(
                "SMTP connect/login failed for %s@%s:%d: %s" %
                (cfg.username, cfg.smtp_host, cfg.smtp_port, e)
            )

        self._smtp = smtp
        self._log_write("SMTP connected to %s:%d as %s." %
                        (cfg.smtp_host, cfg.smtp_port, cfg.username))

    def disconnect(self):
        """Closes both connections, tolerating (and swallowing) any errors so
        shutdown is always clean. Safe to call even if never connected.
        """
        if self._imap is not None:
            try:
                # close() deselects the mailbox; logout() ends the session.
                try:
                    self._imap.close()
                except Exception:
                    pass
                self._imap.logout()
            except Exception:
                pass
            self._imap = None
            self._imap_connect_time = None

        if self._smtp is not None:
            try:
                self._smtp.quit()
            except Exception:
                pass
            self._smtp = None

    def _reconnect_imap(self):
        """Tears down and re-establishes the IMAP connection. Used by
        `idle_wait` to refresh a stale IDLE connection or recover from a drop.
        """
        if self._imap is not None:
            try:
                try:
                    self._imap.close()
                except Exception:
                    pass
                self._imap.logout()
            except Exception:
                pass
            self._imap = None
        self._connect_imap()

    def _require_imap(self):
        """Returns the live IMAP handle, or raises `EmailClientError` if the
        client has not been connected.
        """
        if self._imap is None:
            raise EmailClientError("IMAP is not connected; call connect() first")
        return self._imap

    def _require_smtp(self):
        """Returns the live SMTP handle, or raises `EmailClientError` if the
        client has not been connected.
        """
        if self._smtp is None:
            raise EmailClientError("SMTP is not connected; call connect() first")
        return self._smtp

    @contextlib.contextmanager
    def _imap_op(self, description):
        """Context manager that normalizes RAW transport-level IMAP/socket/SSL
        exceptions raised inside it into `EmailConnectionError`, while letting
        an already-`EmailClientError` (e.g. a non-OK response, or a genuinely-
        unfetchable message) propagate unchanged.

        This gives callers a single, catchable error hierarchy: an
        `EmailConnectionError` means "the connection died mid-command; reconnect
        and retry", whereas a plain `EmailClientError` means "the command ran
        but the result was not OK". The account password is never part of these
        commands, so `str(e)` is safe to include (it is only ever a socket/SSL
        message such as "EOF occurred in violation of protocol").
        """
        try:
            yield
        except EmailClientError:
            # Already normalized (non-OK response, missing payload, etc.).
            raise
        except IMAP_CONNECTION_ERRORS as e:
            raise EmailConnectionError(
                "%s: IMAP connection error (%s: %s)" %
                (description, type(e).__name__, e)
            )

    # --------------------------- Mailbox refresh --------------------------- #
    def refresh(self):
        """Refreshes this connection's view of the selected mailbox so that
        messages which arrived AFTER the last `select()` become visible and
        fetchable on this session.

        A long-lived IMAP connection only knows about the message set it
        learned at `select()` time plus whatever the server has since announced
        via untagged `EXISTS` responses. Per RFC 3501, a server delivers those
        pending untagged responses in reply to ANY command -- and `NOOP` exists
        precisely so a client can poll for them cheaply without side effects.
        Without this poll, a worker connection opened at startup keeps a stale
        message set, so a `UID FETCH` of a just-arrived message returns `OK`
        with no data and the message is invisible to that connection.

        `NOOP` is used rather than a full re-`SELECT` because it is cheap and,
        per the RFC, sufficient to flush pending mailbox updates (Gmail honors
        this); callers needing a hard resync can `disconnect()`/`connect()`.
        Raises `EmailClientError` on a non-OK response.
        """
        imap = self._require_imap()
        with self._imap_op("IMAP NOOP (refresh)"):
            typ, _ = imap.noop()
            if typ != "OK":
                raise EmailClientError("IMAP NOOP (refresh) failed: %s" % typ)

    # ------------------------------ Searching ------------------------------ #
    def search_unseen(self):
        """Returns a list of UID strings for all UNSEEN messages in the selected
        mailbox. Raises `EmailClientError` on a non-OK IMAP response.
        """
        imap = self._require_imap()
        with self._imap_op("UID SEARCH UNSEEN"):
            typ, data = imap.uid("SEARCH", None, "UNSEEN")
            if typ != "OK":
                raise EmailClientError("UID SEARCH UNSEEN failed: %s" % typ)
            # data is like [b"1 2 3"] (or [b""] / [None] when empty).
            raw = data[0] if data else None
            if not raw:
                return []
            if isinstance(raw, bytes):
                raw = raw.decode("ascii", errors="replace")
            return raw.split()

    # ------------------------------- Fetching ------------------------------ #
    def fetch(self, uid):
        """Fetches a full message by UID WITHOUT implicitly marking it `\\Seen`.

        Uses `BODY.PEEK[]` (the peek form) so that merely reading the message
        does not set the `\\Seen` flag; callers decide when to `mark_seen`.
        Returns a `ParsedEmail`. Raises `EmailClientError` on a non-OK response
        or if the UID cannot be found.
        """
        imap = self._require_imap()
        uid = str(uid)
        with self._imap_op("UID FETCH %s" % uid):
            typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK":
                raise EmailClientError("UID FETCH %s failed: %s" % (uid, typ))

            raw = _extract_fetch_payload(data)
            if raw is None:
                raise EmailClientError("UID FETCH %s returned no message data" % uid)

            # Parse into a modern EmailMessage using the default (RFC-compliant)
            # policy so header decoding and `.get_body()` behave sensibly.
            message = email.message_from_bytes(raw, policy=email.policy.default)
            return ParsedEmail(uid, message)

    def mark_seen(self, uid):
        """Explicitly marks the message with the given UID as `\\Seen`. Raises
        `EmailClientError` on a non-OK response.
        """
        imap = self._require_imap()
        with self._imap_op("UID STORE +\\Seen for %s" % uid):
            typ, _ = imap.uid("STORE", str(uid), "+FLAGS", "(\\Seen)")
            if typ != "OK":
                raise EmailClientError("UID STORE +\\Seen for %s failed: %s" % (uid, typ))

    # --------------------------- Reply building ---------------------------- #
    def build_reply(self, original, body_text: str) -> EmailMessage:
        """Builds a properly-threaded reply to `original` (a `ParsedEmail` or a
        raw `EmailMessage`) with the given plain-text body.

        The reply follows RFC 5322 threading conventions so mail clients group
        it into the original conversation:
          * Subject: "Re: <subject>", collapsing an empty subject to just "Re:"
            and never double-prefixing an existing "Re:".
          * To: the original sender's address.
          * From: the configured `from_address`/`username` (optionally with a
            display name).
          * In-Reply-To: the original message's Message-ID.
          * References: the original References (if any) followed by the original
            Message-ID, space-joined.
          * Date / Message-ID: freshly generated.

        Returns the constructed `email.message.EmailMessage`.
        """
        # Normalize `original` into the fields we need, accepting either a
        # ParsedEmail (preferred) or a bare EmailMessage.
        if isinstance(original, ParsedEmail):
            orig_subject = original.subject
            orig_from = original.from_address
            orig_msgid = original.message_id
            orig_refs = original.references
        else:
            orig_subject = _decode_header_value(original.get("Subject"))
            orig_from = email.utils.parseaddr(
                _decode_header_value(original.get("From")))[1].strip().lower()
            orig_msgid = _clean_header(original.get("Message-ID"))
            orig_refs = _clean_header(original.get("References"))

        reply = EmailMessage()
        reply["Subject"] = _reply_subject(orig_subject)
        reply["To"] = orig_from
        reply["From"] = self._reply_from()
        reply["Date"] = email.utils.formatdate(localtime=True)
        reply["Message-ID"] = email.utils.make_msgid()

        # Threading headers: only set when we actually have an original
        # Message-ID to reference.
        if orig_msgid:
            reply["In-Reply-To"] = orig_msgid
            refs = []
            if orig_refs:
                refs.extend(orig_refs.split())
            refs.append(orig_msgid)
            reply["References"] = " ".join(refs)

        reply.set_content(body_text if body_text is not None else "")
        return reply

    def _reply_from(self) -> str:
        """Computes the reply `From:` header value from config, applying the
        optional display name.
        """
        cfg = self.config
        address = cfg.from_address if cfg.from_address else cfg.username
        if cfg.from_name:
            return email.utils.formataddr((cfg.from_name, address))
        return address

    # -------------------------------- Sending ------------------------------ #
    def send(self, message: EmailMessage):
        """Sends a message over the authenticated SMTP connection. If the reply
        has no explicit `From:`, the configured reply identity is filled in.
        Raises `EmailClientError` on any SMTP failure.
        """
        smtp = self._require_smtp()
        if not message.get("From"):
            message["From"] = self._reply_from()
        try:
            smtp.send_message(message)
        except SMTP_CONNECTION_ERRORS as e:
            # Transport-level SMTP failure (dropped/reset socket, SSL error):
            # surface as the reconnect-worthy subtype. `send_message` errors do
            # not carry the password, so `e` is safe to include.
            raise EmailConnectionError("SMTP send failed (connection): %s" % e)
        except Exception as e:
            raise EmailClientError("SMTP send failed: %s" % e)
        self._log_write("Sent message to %s (subject=%r)." %
                        (message.get("To"), message.get("Subject")))

    # --------------------------- Permanent delete -------------------------- #
    def delete(self, uid, message_id=None):
        """PERMANENTLY removes the message with the given UID from the mailbox.

        The strategy depends on `config.delete_mode`:
          * "expunge" (RFC-standard): flag `\\Deleted` on the UID and EXPUNGE.
          * "gmail_trash_expunge" (default, Gmail-correct): copy the message into
            the configured Trash folder, remove it from the source mailbox, then
            select Trash and expunge it there. On Gmail a plain `\\Deleted` +
            EXPUNGE in a folder often only *archives* the message; routing it
            through Trash and expunging Trash is the reliable way to destroy it.
            The original source mailbox is re-selected afterward.

        `message_id` (the original RFC 5322 Message-ID) is used, when provided,
        to precisely locate the copied message inside Trash so only that message
        is expunged. Raises `EmailClientError` on any non-OK IMAP response.

        Fail-safe (gmail_trash_expunge): if the just-copied message cannot be
        unambiguously located in Trash (no `message_id`, or no header match), the
        message is NOT expunged from Trash. It is already removed from the source
        mailbox and left safely in Trash for manual/auto (Gmail 30-day) cleanup;
        a warning is logged. No arbitrary/"newest" message is ever expunged, so
        an unidentified delete can never destroy the wrong email.
        """
        imap = self._require_imap()
        uid = str(uid)

        with self._imap_op("delete uid=%s" % uid):
            if self.config.delete_mode == EMAIL_DELETE_MODE_EXPUNGE:
                self._delete_expunge(imap, uid)
            else:
                self._delete_gmail_trash(imap, uid, message_id)

    def _delete_expunge(self, imap, uid):
        """RFC-standard permanent delete: mark `\\Deleted` and EXPUNGE."""
        typ, _ = imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise EmailClientError("UID STORE +\\Deleted for %s failed: %s" % (uid, typ))
        typ, _ = imap.expunge()
        if typ != "OK":
            raise EmailClientError("EXPUNGE for %s failed: %s" % (uid, typ))
        self._log_write("Permanently deleted message uid=%s (expunge)." % uid)

    def _delete_gmail_trash(self, imap, uid, message_id):
        """Gmail-correct permanent delete: copy to Trash, remove from the source
        mailbox, then expunge the message out of Trash.
        """
        trash = self.config.gmail_trash_folder
        source_mailbox = self._mailbox

        # 1. Copy the message into Trash.
        typ, _ = imap.uid("COPY", uid, trash)
        if typ != "OK":
            raise EmailClientError(
                "UID COPY %s to \"%s\" failed: %s" % (uid, trash, typ)
            )

        # 2. Remove the original from the source mailbox.
        typ, _ = imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise EmailClientError(
                "UID STORE +\\Deleted for %s failed: %s" % (uid, typ)
            )
        typ, _ = imap.expunge()
        if typ != "OK":
            raise EmailClientError("EXPUNGE (source) for %s failed: %s" % (uid, typ))

        # 3. Select Trash and expunge the copied message out of it for good.
        typ, _ = imap.select(trash)
        if typ != "OK":
            raise EmailClientError("failed to select Trash \"%s\": %s" % (trash, typ))

        trash_error = None
        try:
            trash_uids = self._find_trash_uids(imap, message_id)
            if not trash_uids:
                # FAIL-SAFE: we could not unambiguously identify the copied
                # message in Trash (no Message-ID, or no header match). We MUST
                # NOT expunge an arbitrary/"newest" message -- doing so could
                # permanently destroy an unrelated email that happens to sit in
                # Trash. The message has already been removed from the source
                # mailbox and now rests safely in Trash; leave it there for
                # manual or automatic (Gmail purges Trash after ~30 days)
                # cleanup. The delete is still considered successful: the
                # message is gone from the inbox and is NOT lost.
                self._log_write(
                    "WARNING: could not unambiguously identify uid=%s in Trash "
                    "\"%s\" (message_id=%r); skipping Trash expunge to avoid "
                    "deleting the wrong message. It remains in Trash for "
                    "manual/auto cleanup." % (uid, trash, message_id)
                )
            else:
                for tuid in trash_uids:
                    typ, _ = imap.uid("STORE", tuid, "+FLAGS", "(\\Deleted)")
                    if typ != "OK":
                        raise EmailClientError(
                            "UID STORE +\\Deleted in Trash for %s failed: %s"
                            % (tuid, typ)
                        )
                typ, _ = imap.expunge()
                if typ != "OK":
                    raise EmailClientError("EXPUNGE (Trash) failed: %s" % typ)
        except EmailClientError as e:
            # Defer raising until AFTER we have attempted to re-select the source
            # mailbox, so the connection is restored to a usable state (or the
            # re-select failure is surfaced) regardless.
            trash_error = e

        # Always re-select the original mailbox so the client stays usable. A
        # failed re-SELECT means the connection is in a bad state: surface it as
        # an EmailClientError so the caller/listener forces a reconnect rather
        # than continuing on a broken connection.
        rtyp, _ = imap.select(source_mailbox)
        if rtyp == "OK":
            self._mailbox = source_mailbox
            if trash_error is not None:
                raise trash_error
        else:
            msg = (
                "failed to re-select source mailbox \"%s\" after delete "
                "(rtyp=%s); forcing reconnect" % (source_mailbox, rtyp)
            )
            if trash_error is not None:
                msg += "; prior Trash error: %s" % trash_error
            raise EmailClientError(msg)

        self._log_write("Permanently deleted message uid=%s (gmail_trash_expunge)." % uid)

    def _find_trash_uids(self, imap, message_id):
        """Returns the UID(s) of the just-trashed message inside the Trash
        folder, identified UNAMBIGUOUSLY by its RFC 5322 Message-ID.

        Returns an empty list when the message cannot be unambiguously located
        -- i.e. when no `message_id` is known, or the header SEARCH returns no
        match. Callers MUST treat an empty result as "do not expunge anything":
        we deliberately never fall back to guessing (e.g. the newest message in
        Trash), because that could permanently destroy an unrelated email.
        """
        if not message_id:
            return []
        typ, data = imap.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
        if typ == "OK" and data and data[0]:
            raw = data[0]
            if isinstance(raw, bytes):
                raw = raw.decode("ascii", errors="replace")
            uids = raw.split()
            if uids:
                return uids
        return []

    # -------------------------------- IDLE --------------------------------- #
    def idle_wait(self, timeout) -> bool:
        """Issues IMAP IDLE and blocks until a new-mail / EXISTS event arrives or
        `timeout` seconds elapse. Returns True if a new-mail event was observed,
        False on timeout.

        Resilience:
          * Before starting, if the IMAP connection has been open longer than
            `config.idle_refresh_interval` (~29 min), it is proactively
            reconnected so the IDLE is always issued on a fresh connection.
          * The IDLE block is capped at `idle_refresh_interval` so we re-issue
            IDLE before the server would drop it, even for long `timeout` values.
          * If the connection drops mid-IDLE, it is transparently reconnected and
            the method returns False so the caller can loop and try again (and
            re-run `search_unseen`, catching anything that arrived meanwhile).
        """
        self._require_imap()

        # Proactive refresh: reconnect if the connection is older than the
        # configured refresh interval.
        if self._imap_age() >= self.config.idle_refresh_interval > 0:
            self._log_write("Refreshing stale IMAP IDLE connection.")
            try:
                self._reconnect_imap()
            except EmailClientError:
                # Surface a connect failure so the caller can back off/notify.
                raise

        # Cap how long a single IDLE blocks so it is renewed before the server's
        # ~29 minute ceiling.
        wait_secs = timeout
        if self.config.idle_refresh_interval > 0:
            wait_secs = min(timeout, self.config.idle_refresh_interval)

        try:
            return self._idle_once(wait_secs)
        except (OSError, imaplib.IMAP4.abort, imaplib.IMAP4.error) as e:
            # Connection dropped or misbehaved mid-IDLE: reconnect and report no
            # event for this cycle.
            self._log_write("IMAP IDLE interrupted (%s); reconnecting." % e)
            self._reconnect_imap()
            return False

    def _imap_age(self):
        """Returns the number of seconds since the IMAP connection was last
        (re)established, or a very large number if not connected.
        """
        if self._imap_connect_time is None:
            return float("inf")
        return time.monotonic() - self._imap_connect_time

    def _idle_once(self, wait_secs) -> bool:
        """Runs a single IMAP IDLE cycle on the current connection, blocking up
        to `wait_secs` seconds. Returns True if an EXISTS/RECENT (new mail)
        untagged response was seen, else False. Always issues DONE to leave IDLE
        cleanly. Raises on connection loss (handled by the caller).

        imaplib has no IDLE helper, so this drives the raw protocol: send the
        `IDLE` command, read the "+ idling" continuation, wait on the socket for
        untagged server pushes, then send `DONE` and drain the tagged completion.
        """
        imap = self._imap
        tag = imap._new_tag()

        # Begin IDLE. The server should answer with a "+ idling" continuation.
        imap.send(b"%s IDLE\r\n" % tag)
        resp = imap.readline()
        if not resp:
            raise imaplib.IMAP4.abort("connection closed at IDLE start")

        event = False
        sock = imap.socket()
        deadline = time.monotonic() + max(0, wait_secs)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break  # timed out with no new-mail event
                readable, _, _ = select.select([sock], [], [], remaining)
                if not readable:
                    break  # select timeout -> no data
                line = imap.readline()
                if not line:
                    raise imaplib.IMAP4.abort("connection closed during IDLE")
                upper = line.upper()
                # A new message bumps EXISTS; RECENT also signals arrival.
                if b"EXISTS" in upper or b"RECENT" in upper:
                    event = True
                    break
        finally:
            # Always terminate IDLE and drain the tagged completion so the
            # connection is left in a clean, reusable state.
            imap.send(b"DONE\r\n")
            self._drain_until_tag(imap, tag)
        return event

    def _drain_until_tag(self, imap, tag):
        """Reads and discards server lines until the tagged completion for our
        IDLE command (or the connection ends). Bounded so a misbehaving server
        cannot spin forever.
        """
        if isinstance(tag, str):
            tag_bytes = tag.encode("ascii")
        else:
            tag_bytes = tag
        for _ in range(1000):
            line = imap.readline()
            if not line:
                return
            if line.startswith(tag_bytes):
                return


# ============================ Module Helpers =============================== #
def _decode_header_value(value) -> str:
    """Decodes a possibly MIME-encoded ("=?UTF-8?...?=") header value into a
    plain string. Returns "" for a missing (None) header.
    """
    if value is None:
        return ""
    try:
        parts = email.header.decode_header(str(value))
        return str(email.header.make_header(parts))
    except Exception:
        return str(value)


def _clean_header(value) -> str:
    """Normalizes a threading header (Message-ID / In-Reply-To / References) to a
    whitespace-collapsed string, or "" when absent.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())


def _extract_plain_text(message: EmailMessage) -> str:
    """Extracts a decoded plain-text body from a message, preferring text/plain
    and falling back to a naive strip of text/html only if no plain part exists.
    """
    # `EmailMessage.get_body` (default policy) walks the MIME tree for us.
    try:
        part = message.get_body(preferencelist=("plain",))
        if part is not None:
            return part.get_content()
        part = message.get_body(preferencelist=("html",))
        if part is not None:
            return _html_to_text(part.get_content())
    except Exception:
        pass

    # Fallback for non-multipart or legacy Message objects.
    try:
        payload = message.get_payload(decode=True)
        if payload is not None:
            charset = message.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    except Exception:
        pass
    return ""


def _html_to_text(html: str) -> str:
    """A deliberately minimal HTML-to-text conversion (strip tags). Only used
    when a message has no text/plain part at all.
    """
    import re
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return text.strip()


def _reply_subject(subject: str) -> str:
    """Builds the reply subject: 'Re: <subject>', collapsing an empty subject to
    'Re:' and never double-prefixing an existing (case-insensitive) 'Re:'.
    """
    subj = (subject or "").strip()
    if subj == "":
        return "Re:"
    if subj[:3].lower() == "re:":
        return subj
    return "Re: " + subj


def _extract_fetch_payload(data):
    """Pulls the raw RFC822 message bytes out of an imaplib FETCH response.

    imaplib returns something like:
        [ (b'1 (UID 5 BODY[] {N}', b'<raw message bytes>'), b')' ]
    so we look for the first tuple element and return its second item (the
    literal). Returns None if no literal is present.
    """
    if not data:
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            payload = item[1]
            if isinstance(payload, bytes):
                return payload
            if isinstance(payload, str):
                return payload.encode("utf-8", errors="replace")
    return None
