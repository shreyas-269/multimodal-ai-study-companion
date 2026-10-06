"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/components/auth-provider";
import { patchMe, type MePatchBody, type MePatchResponse } from "@/lib/api";

export function usePatchMe() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const uid = user?.uid;

  return useMutation({
    mutationFn: (body: MePatchBody): Promise<MePatchResponse> => patchMe(body),
    retry: false,
    onSuccess: (returnedUser: MePatchResponse) => {
      queryClient.setQueryData(["me", uid], returnedUser);
    },
  });
}
