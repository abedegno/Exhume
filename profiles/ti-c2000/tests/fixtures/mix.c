/* The source of dis_mix.txt: dis2000's listing of this file compiled at -O2, which pairs FPU
   instructions with parallel moves. */
float mix(float *a, float *b, float k)
{
    float s = a[0] * k;
    float t = b[0] * k;
    a[1] = s;
    b[1] = t;
    return s * t + a[2] * b[2];
}
