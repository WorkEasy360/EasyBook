import { z } from "zod";

/**
 * Client-side checks for the sign-up form: early feedback only. The backend
 * (accounts/serializers.py RegisterSerializer + AUTH_PASSWORD_VALIDATORS) is
 * the authority and also rejects common passwords and ones too similar to the
 * email or name, which cannot be judged here.
 */

/** backend/config/settings/base.py MinimumLengthValidator. */
export const PASSWORD_MIN_LENGTH = 10;
/** User.first_name / last_name max_length. */
const NAME_MAX_LENGTH = 150;

export const registerSchema = z
  .object({
    first_name: z.string().trim().max(NAME_MAX_LENGTH, `Use at most ${NAME_MAX_LENGTH} characters.`),
    last_name: z.string().trim().max(NAME_MAX_LENGTH, `Use at most ${NAME_MAX_LENGTH} characters.`),
    email: z.email({ message: "Enter a valid email address." }),
    password: z
      .string()
      .min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`)
      // NumericPasswordValidator
      .refine((value) => !/^\d+$/.test(value), "Use letters or symbols as well as numbers."),
    confirm_password: z.string().min(1, "Enter your password again."),
  })
  .refine((values) => values.password === values.confirm_password, {
    path: ["confirm_password"],
    message: "The passwords do not match.",
  });

export type RegisterValues = z.infer<typeof registerSchema>;

export const EMPTY_REGISTER_VALUES: RegisterValues = {
  first_name: "",
  last_name: "",
  email: "",
  password: "",
  confirm_password: "",
};

/** Fields the backend reports errors against that have an input on the form. */
export const SERVER_FIELDS = ["first_name", "last_name", "email", "password"] as const;
export type ServerField = (typeof SERVER_FIELDS)[number];

export function isServerField(field: string): field is ServerField {
  return (SERVER_FIELDS as readonly string[]).includes(field);
}
