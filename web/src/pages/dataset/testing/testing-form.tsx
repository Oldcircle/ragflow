'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { useForm, useWatch } from 'react-hook-form';
import { z } from 'zod';

import { CrossLanguageFormField } from '@/components/cross-language-form-field';
import { FormContainer } from '@/components/form-container';
import {
  MetadataFilter,
  MetadataFilterSchema,
} from '@/components/metadata-filter';
import {
  RerankFormFields,
  initialTopKValue,
  topKSchema,
} from '@/components/rerank';
import {
  SimilaritySliderFormField,
  initialSimilarityThresholdValue,
  initialVectorSimilarityWeightValue,
  similarityThresholdSchema,
  vectorSimilarityWeightSchema,
} from '@/components/similarity-slider';
import { ButtonLoading } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from '@/components/ui/form';
import { Textarea } from '@/components/ui/textarea';
import { UseKnowledgeGraphFormField } from '@/components/use-knowledge-graph-item';
import { useTestRetrieval } from '@/hooks/use-knowledge-request';
import { trim } from 'lodash';
import { Send } from 'lucide-react';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';

type TestingFormProps = Pick<
  ReturnType<typeof useTestRetrieval>,
  'loading' | 'refetch' | 'setValues'
>;

export default function TestingForm({
  loading,
  refetch,
  setValues,
}: TestingFormProps) {
  const { t } = useTranslation();
  const { id } = useParams();
  const knowledgeBaseId = id;

  const formSchema = z.object({
    question: z.string().min(1, {
      message: t('knowledgeDetails.testTextPlaceholder'),
    }),
    ...similarityThresholdSchema,
    ...vectorSimilarityWeightSchema,
    ...topKSchema,
    use_kg: z.boolean().optional(),
    kb_ids: z.array(z.string()).optional(),
    ...MetadataFilterSchema,
  });

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      ...initialSimilarityThresholdValue,
      ...initialVectorSimilarityWeightValue,
      ...initialTopKValue,
      use_kg: false,
      kb_ids: [knowledgeBaseId],
    },
  });

  const question = form.watch('question');

  const values = useWatch({ control: form.control });

  useEffect(() => {
    setValues(values as Required<z.infer<typeof formSchema>>);
  }, [setValues, values]);

  function onSubmit() {
    refetch();
  }

  return (
    <Form {...form}>
      <form
        className="flex size-full min-h-0 flex-col"
        onSubmit={form.handleSubmit(onSubmit)}
        data-testid="dataset-testing-form"
      >
        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          <FormContainer className="h-full p-5">
            <SimilaritySliderFormField isTooltipShown={true} />
            <RerankFormFields />
            <UseKnowledgeGraphFormField name="use_kg" />
            <CrossLanguageFormField name={'cross_languages'} />
            <MetadataFilter prefix="" />
          </FormContainer>
        </div>

        <footer className="border-t border-border-button bg-bg-component/60 px-5 py-4">
          <FormField
            control={form.control}
            name="question"
            render={({ field }) => (
              <FormItem>
                <FormControl>
                  <Textarea
                    {...field}
                    placeholder={t('knowledgeDetails.testTextPlaceholder')}
                    data-testid="dataset-testing-query"
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="mt-3 text-end">
            <ButtonLoading
              type="submit"
              disabled={!trim(question)}
              loading={loading}
              data-testid="dataset-testing-submit"
            >
              {t('knowledgeDetails.testingLabel')}
              <Send />
            </ButtonLoading>
          </div>
        </footer>
      </form>
    </Form>
  );
}
